"""ChunkClassifier: classify article chunks via the LLM, with caching & tolerance."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from json_repair import repair_json

from ..config import Config
from ..jsonio import get_logger, load_env_secret, save_json
from ..paths import ProjectPaths
from ..text import extract_json
from .cache import ChunkCache
from .client import LLMClient, UsageTracker

log = get_logger("classifier")

STRICTER_PROMPT_PREFIX = (
    "CRITICAL: respond with ONLY valid JSON.\n"
    "No markdown fences. No comments. No trailing commas. "
    "No unescaped quotes inside strings.\n"
    "Begin your response with { and end with }.\n\n"
)


class UsageLog:
    """Append-only per-run token-usage records at data/llm_usage.jsonl."""

    def __init__(self, path: str):
        self.path = path

    def append(self, record: dict) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("could not append usage record to %s: %s", self.path, e)

    def read(self, limit: int = 0) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        records: list[dict] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records[-limit:] if limit > 0 else records


class ChunkClassifier:
    def __init__(self, config: Config, paths: ProjectPaths, transport=None,
                 prompt_path: str | None = None):
        self.config = config
        self.paths = paths
        self.provider = config.llm.active_provider
        self.chunks_dir = paths.data("chunks")
        self.failed_dir = paths.data("failed_chunks")
        self.prompt_path = prompt_path or self._resolve_prompt_path()
        with open(self.prompt_path, encoding="utf-8") as f:
            self.prompt_template = f.read()
        namespace = "|".join((
            self.provider.name, self.provider.model, str(self.provider.temperature),
            hashlib.sha256(self.prompt_template.encode("utf-8")).hexdigest(),
        ))
        self.cache = ChunkCache(paths.data("chunk_cache"), namespace)
        self.usage = UsageTracker()
        api_key = (load_env_secret(self.provider.api_key_env, paths.env_path)
                   if self.provider.api_key_env else None)
        self.client = LLMClient(self.provider, api_key, self.usage, transport)
        self.usage_log = UsageLog(paths.data("llm_usage.jsonl"))
        self._lock = Lock()
        self._cache_hits = 0
        self._cache_misses = 0

    def _resolve_prompt_path(self) -> str:
        override = os.path.join(self.paths.prompts_dir, f"classify.{self.provider.name}.txt")
        default = os.path.join(self.paths.prompts_dir, "classify.txt")
        if os.path.exists(override):
            return override
        if not os.path.exists(default):
            raise FileNotFoundError(f"missing classification prompt: {default}")
        return default

    def _dump_failed(self, label: str, raw: str) -> str | None:
        try:
            os.makedirs(self.failed_dir, exist_ok=True)
            path = os.path.join(self.failed_dir, f"{label}_{int(time.time())}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(raw)
            return path
        except OSError as e:
            log.warning("%s: could not save failed output: %s", label, e)
            return None

    def parse(self, content: str, label: str) -> dict | None:
        """Strict JSON parse with a json-repair fallback for sloppy LLM output."""
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            log.warning("%s: invalid JSON (%s); attempting repair", label, e)
        try:
            parsed = json.loads(repair_json(content, return_objects=False))
            log.info("%s: JSON repaired successfully", label)
            return parsed
        except (ValueError, json.JSONDecodeError) as e:
            path = self._dump_failed(label, content)
            log.error("%s: JSON unrepairable: %s%s", label, e,
                      f" (raw saved to {path})" if path else "")
            return None

    def _classify_text(self, label: str, prompt: str) -> dict | None:
        """One completion + parse, with the stricter-prompt retry on bad JSON."""
        result = self.client.complete(label, prompt)
        if result.content is not None:
            parsed = self.parse(extract_json(result.content), label)
            if parsed is not None:
                return parsed
        if self.config.llm.bad_json_retry:
            log.warning("%s: re-attempting with stricter JSON prompt", label)
            retry = self.client.complete(label, STRICTER_PROMPT_PREFIX + prompt)
            if retry.content is not None:
                return self.parse(extract_json(retry.content), label)
        return None

    def classify_chunk(self, n: int) -> bool:
        label = f"chunk_{n}"
        chunk_path = os.path.join(self.chunks_dir, f"chunk_{n}.json")
        out_path = os.path.join(self.chunks_dir, f"chunk_{n}_classified.json")
        with open(chunk_path, encoding="utf-8") as f:
            articles = json.load(f).get("articles", [])

        cached = self.cache.get(articles)
        if cached is not None:
            save_json(out_path, cached)
            with self._lock:
                self._cache_hits += 1
            log.info("%s: cache hit, skipping API call", label)
            return True
        with self._lock:
            self._cache_misses += 1

        if self.provider.api_key_env and not self.client.api_key:
            log.error("%s: secret '%s' not set in env or .env", label, self.provider.api_key_env)
            return False

        articles_json = json.dumps(articles, ensure_ascii=False, indent=2)
        prompt = self.prompt_template.format(n_articles=len(articles), articles_json=articles_json)
        log.info("%s: sending %d articles to %s (%d chars)",
                 label, len(articles), self.provider.model, len(prompt))

        parsed = self._classify_text(label, prompt)
        if parsed is None:
            return False

        output = {"chunk_size": len(articles), "stories": parsed.get("stories", [])}
        save_json(out_path, output)
        self.cache.put(articles, output)
        log.info("%s: %d stories → %s", label, len(output["stories"]), out_path)
        return True

    def _discover_chunk_nums(self) -> list[int]:
        pattern = os.path.join(self.chunks_dir, "chunk_*.json")
        files = [f for f in glob.glob(pattern) if "_classified" not in os.path.basename(f)]
        return sorted(int(os.path.basename(f).removeprefix("chunk_").removesuffix(".json"))
                      for f in files)

    def _record_usage(self, chunks_total: int) -> None:
        snap = self.usage.snapshot()
        self.usage_log.append({
            "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": self.provider.name, "model": self.provider.model,
            "chunks_total": chunks_total, "cache_hits": self._cache_hits,
            "api_calls": snap["api_calls"], "prompt_tokens": snap["prompt_tokens"],
            "completion_tokens": snap["completion_tokens"],
        })

    def classify_all(self) -> bool:
        self.cache.prune(self.config.llm.cache_retention_days)
        self.usage.reset()
        self._cache_hits = self._cache_misses = 0

        chunk_nums = self._discover_chunk_nums()
        if not chunk_nums:
            log.error("no chunk files found in %s", self.chunks_dir)
            return False

        log.info("classify: provider=%s (%s, model=%s); prompt=%s",
                 self.provider.name, self.provider.base_url, self.provider.model, self.prompt_path)
        log.info("classifying %d chunks with %d workers",
                 len(chunk_nums), self.config.llm.classify_concurrency)

        failures: list[int] = []
        with ThreadPoolExecutor(max_workers=self.config.llm.classify_concurrency) as pool:
            futures = {pool.submit(self.classify_chunk, n): n for n in chunk_nums}
            for fut in as_completed(futures):
                n = futures[fut]
                try:
                    ok = fut.result()
                except Exception as e:  # noqa: BLE001
                    log.error("chunk_%d: unhandled exception: %s", n, e)
                    ok = False
                if not ok:
                    failures.append(n)

        snap = self.usage.snapshot()
        log.info("chunk_cache: %d hits, %d misses", self._cache_hits, self._cache_misses)
        log.info("llm usage: %d api calls, %d prompt + %d completion tokens",
                 snap["api_calls"], snap["prompt_tokens"], snap["completion_tokens"])
        self._record_usage(len(chunk_nums))

        if not failures:
            log.info("classify: all %d chunks succeeded", len(chunk_nums))
            return True

        ratio = len(failures) / len(chunk_nums)
        tol = self.config.llm.failure_tolerance
        if ratio <= tol and len(failures) < len(chunk_nums):
            log.warning("classify: %d/%d chunks failed (%.0f%%) — within tolerance %.0f%%, "
                        "proceeding with partial results: %s",
                        len(failures), len(chunk_nums), ratio * 100, tol * 100, sorted(failures))
            return True
        log.error("classify: %d/%d chunks failed (%.0f%%) — exceeded tolerance %.0f%%: %s",
                  len(failures), len(chunk_nums), ratio * 100, tol * 100, sorted(failures))
        return False
