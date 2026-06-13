"""Command-line interface: `python -m news_digest <command>` (and `news-digest`)."""

from __future__ import annotations

import argparse
import json
import sys

from .chunking import Chunker, validate_classified
from .classify.classifier import ChunkClassifier, UsageLog
from .config import Config, ConfigError
from .delivery.telegram import TelegramNotifier
from .digest.archive import DigestArchive
from .digest.assembler import DigestAssembler
from .digest.merge import ChunkMerger
from .digest.render import TelegramRenderer
from .digest.rollup import WeeklyRollup
from .jsonio import get_logger
from .models import DigestDocument
from .monitoring.health import CHECK_LABELS, HealthChecker
from .monitoring.history import RunHistory
from .monitoring.watchdog import Watchdog
from .paths import DEFAULT_PATHS
from .pipeline import Pipeline
from .sources.metrics import Reweighter, SourceMetrics
from .sources.scraper import Scraper
from .training.stats import DatasetStats

log = get_logger("cli")
_EXIT = {"healthy": 0, "warning": 1, "critical": 2}


def _cmd_run(args, config, paths) -> int:
    return Pipeline(config, paths).run(resume=args.resume)


def _cmd_scrape(args, config, paths) -> int:
    Scraper(config, paths).run()
    return 0


def _cmd_extract(args, config, paths) -> int:
    Chunker(config, paths).extract()
    return 0


def _cmd_split(args, config, paths) -> int:
    Chunker(config, paths).split()
    return 0


def _cmd_classify(args, config, paths) -> int:
    return 0 if ChunkClassifier(config, paths).classify_all() else 1


def _cmd_merge(args, config, paths) -> int:
    ChunkMerger(config, paths).run()
    return 0


def _cmd_assemble(args, config, paths) -> int:
    DigestAssembler(config, paths).assemble()
    return 0


def _cmd_cleanup(args, config, paths) -> int:
    Chunker(config, paths).cleanup()
    return 0


def _cmd_validate(args, config, paths) -> int:
    validate_classified(paths, config)
    return 0


def _cmd_format(args, config, paths) -> int:
    return _render_digest_path(config, paths.data("digest.json"))


def _cmd_deliver(args, config, paths) -> int:
    return TelegramNotifier(config, paths).deliver()


def _render_digest_path(config, path) -> int:
    import os
    if not os.path.exists(path):
        log.error("digest not found at %s", path)
        return 1
    doc = DigestDocument.from_dict(json.load(open(path, encoding="utf-8")))
    print(TelegramRenderer(config.categories).render(doc))
    return 0


def _cmd_health(args, config, paths) -> int:
    report = HealthChecker(config, paths).run(max_age=args.max_age_minutes)
    overall = report["overall"]
    if args.json:
        print(json.dumps({"overall": overall, "checks": {
            k: {"status": v["status"], "issues": v["issues"]} for k, v in report["checks"].items()
        }}, ensure_ascii=False))
        return _EXIT[overall]
    icons = {"healthy": "✅", "warning": "⚠️", "critical": "❌"}
    print(f"📊 Health check: {overall.upper()}\n")
    for name, check in report["checks"].items():
        label = CHECK_LABELS.get(name, (name, name))[0]
        print(f"  {icons[check['status']]} {label}: {check['status']}")
        for severity, msg in check.get("issues", []):
            print(f"     [{severity}] {msg}")
    print(f"\nReport saved: {paths.data('health.json')}")
    return _EXIT[overall]


def _cmd_watchdog(args, config, paths) -> int:
    return Watchdog(config, paths).run()


def _cmd_weekly(args, config, paths) -> int:
    doc = WeeklyRollup(config, paths).build(days=args.days)
    if doc is None:
        print(f"(no digest snapshots within the last {args.days} days)")
        return 1
    print(TelegramRenderer(config.categories).render(doc))
    return 0


def _cmd_usage(args, config, paths) -> int:
    records = UsageLog(paths.data("llm_usage.jsonl")).read(args.limit)
    if not records:
        print("(no usage records yet)")
        return 0
    fmt = "{:<20}  {:<28}  {:>6}  {:>5}  {:>5}  {:>9}  {:>11}"
    print(fmt.format("run_at", "model", "chunks", "cache", "calls", "prompt_tk", "complete_tk"))
    print(fmt.format(*("-" * w for w in (20, 28, 6, 5, 5, 9, 11))))
    tp = tc = 0
    for r in records:
        tp += r.get("prompt_tokens", 0)
        tc += r.get("completion_tokens", 0)
        print(fmt.format(r.get("run_at", "?"), str(r.get("model", "?"))[:28],
                         r.get("chunks_total", "?"), r.get("cache_hits", "?"),
                         r.get("api_calls", "?"), r.get("prompt_tokens", 0),
                         r.get("completion_tokens", 0)))
    print(f"\nΣ over {len(records)} run(s): {tp} prompt + {tc} completion = {tp + tc} tokens")
    return 0


def _cmd_history(args, config, paths) -> int:
    records = RunHistory(paths.data("pipeline_history.jsonl")).read(args.limit)
    print(RunHistory.format_table(records))
    return 0


def _cmd_dataset_stats(args, config, paths) -> int:
    print(DatasetStats(paths.data("training", "dataset.jsonl")).format_report())
    return 0


def _cmd_show(args, config, paths) -> int:
    archive = DigestArchive(paths.data("digests"), config.archive.retention_days)
    renderer = TelegramRenderer(config.categories)
    if args.list:
        paths_ = archive.snapshot_paths()
        if not paths_:
            print("(no snapshots)")
            return 0
        import os
        for p in paths_:
            ts = os.path.basename(p).removeprefix("digest-").removesuffix(".json")
            try:
                headline = archive.load(p).digest.headline
            except (ValueError, OSError):
                headline = "(unreadable)"
            print(f"{ts}  {headline[:100]}")
        return 0
    if args.latest:
        snaps = archive.snapshot_paths()[-args.latest:]
        if not snaps:
            print("(no snapshots)")
            return 0
        for i, p in enumerate(snaps):
            if i:
                print("\n" + "─" * 60 + "\n")
            print(renderer.render(archive.load(p)))
        return 0
    if args.snapshot:
        p = archive.resolve(args.snapshot)
        if p is None:
            log.error("no snapshot matches '%s'", args.snapshot)
            return 1
        print(renderer.render(archive.load(p)))
        return 0
    return _render_digest_path(config, paths.data("digest.json"))


def _cmd_tune(args, config, paths) -> int:
    metrics = SourceMetrics(paths.data("source_metrics.json"))
    changes = Reweighter(paths.sources_path, metrics).propose(apply=args.apply)
    if not changes:
        print("(no proposed weight changes)")
        return 0
    for name, old, new in changes:
        print(f"  {name:<24}  {old:.2f} → {new:.2f}  ({new - old:+.2f})")
    print(f"\n{'Wrote' if args.apply else 'Would write'} {len(changes)} change(s) to sources.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="news-digest", description="News digest pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full pipeline")
    p_run.add_argument("--resume", action="store_true", help="Skip scrape/extract/split")
    p_run.set_defaults(func=_cmd_run)

    for name, fn, help_ in [
        ("scrape", _cmd_scrape, "Scrape feeds → raw_news.json"),
        ("extract", _cmd_extract, "raw_news.json → articles.json"),
        ("split", _cmd_split, "articles.json → chunk files"),
        ("classify", _cmd_classify, "Classify all chunks via LLM"),
        ("merge", _cmd_merge, "Merge + dedup → classified.json"),
        ("assemble", _cmd_assemble, "Build digest.json + digest.md"),
        ("format", _cmd_format, "Render current digest to stdout"),
        ("deliver", _cmd_deliver, "Send digest to Telegram"),
        ("cleanup", _cmd_cleanup, "Remove chunk files"),
        ("validate", _cmd_validate, "Validate classified.json structure"),
        ("watchdog", _cmd_watchdog, "Health watchdog → alert (cron)"),
        ("dataset-stats", _cmd_dataset_stats, "Training-dataset stats"),
    ]:
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(func=fn)

    p_health = sub.add_parser("health", help="Pipeline health check")
    p_health.add_argument("--json", action="store_true")
    p_health.add_argument("--max-age-minutes", type=int, default=None)
    p_health.set_defaults(func=_cmd_health)

    p_weekly = sub.add_parser("weekly", help="Weekly rollup digest")
    p_weekly.add_argument("--days", type=int, default=7)
    p_weekly.set_defaults(func=_cmd_weekly)

    p_usage = sub.add_parser("usage", help="LLM token usage per run")
    p_usage.add_argument("--limit", type=int, default=20)
    p_usage.set_defaults(func=_cmd_usage)

    p_history = sub.add_parser("history", help="Pipeline run history")
    p_history.add_argument("--limit", type=int, default=20)
    p_history.set_defaults(func=_cmd_history)

    p_show = sub.add_parser("show", help="View current or archived digest")
    p_show.add_argument("snapshot", nargs="?", help="Snapshot timestamp prefix")
    p_show.add_argument("--list", action="store_true")
    p_show.add_argument("--latest", type=int, default=0)
    p_show.set_defaults(func=_cmd_show)

    p_tune = sub.add_parser("tune", help="Propose/apply source reweighting")
    p_tune.add_argument("--apply", action="store_true")
    p_tune.set_defaults(func=_cmd_tune)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.load()
    except ConfigError as e:
        log.error("%s", e)
        return 2
    return args.func(args, config, DEFAULT_PATHS)


if __name__ == "__main__":
    sys.exit(main())
