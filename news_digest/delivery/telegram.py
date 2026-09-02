"""TelegramNotifier: deliver the digest or an ops alert via the Bot API.

Both paths no-op (exit 0) when their config flag is off so callers can invoke
them unconditionally. Digest delivery is gated by telegram.enabled; alerts by
telegram.alerts (independent), with identical consecutive alerts suppressed.
"""

from __future__ import annotations

import hashlib
import os
import time

import httpx

from ..config import Config
from ..digest.render import TelegramRenderer, split_message
from ..jsonio import get_logger, load_env_secret, load_json
from ..models import DigestDocument
from ..paths import ProjectPaths

log = get_logger("telegram")

API_BASE = "https://api.telegram.org"
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 30


class TelegramNotifier:
    def __init__(self, config: Config, paths: ProjectPaths,
                 renderer: TelegramRenderer | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.config = config
        self.paths = paths
        self.renderer = renderer or TelegramRenderer(config.categories)
        self._transport = transport

    def _client(self) -> httpx.Client:
        kwargs = {"timeout": REQUEST_TIMEOUT}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _credentials(self) -> tuple[str, str]:
        return (load_env_secret("TELEGRAM_BOT_TOKEN", self.paths.env_path),
                load_env_secret("TELEGRAM_CHAT_ID", self.paths.env_path))

    def send_message(self, client: httpx.Client, token: str, chat_id: str, text: str,
                     sleep=time.sleep) -> bool:
        """Send one message. Retries 429 (honoring retry_after) and 5xx."""
        url = f"{API_BASE}/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True,
                   "parse_mode": "Markdown"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = client.post(url, json=payload)
            except httpx.RequestError as e:
                log.warning("attempt %d/%d: request error: %s", attempt, MAX_ATTEMPTS, e)
                if attempt < MAX_ATTEMPTS:
                    sleep(2 ** attempt)
                continue
            if resp.status_code == 200:
                return True
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = None
                if resp.status_code == 429:
                    try:
                        retry_after = resp.json().get("parameters", {}).get("retry_after")
                    except ValueError:
                        pass
                log.warning("attempt %d/%d: HTTP %d: %s", attempt, MAX_ATTEMPTS,
                            resp.status_code, resp.text[:200])
                if attempt < MAX_ATTEMPTS:
                    sleep(retry_after if retry_after else 2 ** attempt)
                continue
            log.error("HTTP %d (not retryable): %s", resp.status_code, resp.text[:200])
            return False
        log.error("all %d attempts failed", MAX_ATTEMPTS)
        return False

    def deliver(self, digest_path: str | None = None) -> int:
        """Deliver data/digest.json. Returns a process exit code."""
        if not self.config.telegram.enabled:
            log.info("telegram.enabled is false — skipping delivery (no-op)")
            return 0
        token, chat_id = self._credentials()
        if not token or not chat_id:
            log.error("telegram.enabled is true but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                      "is not set in env or .env")
            return 1
        digest_path = digest_path or self.paths.data("digest.json")
        if not os.path.exists(digest_path):
            log.error("digest not found at %s — run the pipeline first", digest_path)
            return 1

        doc = DigestDocument.from_dict(load_json(digest_path))
        parts = split_message(self.renderer.render(doc), self.config.telegram.message_limit)
        log.info("Delivering digest in %d message(s)", len(parts))
        with self._client() as client:
            for i, part in enumerate(parts, 1):
                text = f"[{i}/{len(parts)}]\n{part}" if len(parts) > 1 else part
                if not self.send_message(client, token, chat_id, text):
                    log.error("delivery failed at part %d/%d", i, len(parts))
                    return 1
        log.info("Delivered %d message(s)", len(parts))
        return 0

    def send_alert(self, text: str, hash_path: str | None = None) -> int:
        """Send `text` as an ops alert, suppressing identical consecutive alerts."""
        if not self.config.telegram.alerts:
            log.info("telegram.alerts is false — skipping alert (no-op)")
            return 0
        text = text.strip()
        if not text:
            return 0
        token, chat_id = self._credentials()
        if not token or not chat_id:
            log.error("telegram.alerts is true but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                      "is not set in env or .env")
            return 1

        hash_path = hash_path or self.paths.data(".last_alert_hash")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            with open(hash_path, encoding="utf-8") as f:
                if f.read().strip() == digest:
                    log.info("identical alert already sent — suppressing resend")
                    return 0
        except OSError:
            pass

        with self._client() as client:
            for part in split_message(text, self.config.telegram.message_limit):
                if not self.send_message(client, token, chat_id, part):
                    log.error("alert delivery failed")
                    return 1
        try:
            with open(hash_path, "w", encoding="utf-8") as f:
                f.write(digest)
        except OSError as e:
            log.warning("could not record alert hash at %s: %s", hash_path, e)
        log.info("Alert delivered")
        return 0
