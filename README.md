# news-digest

A daily Russian-language news digest pipeline. It scrapes ~10 independent RSS sources
(Meduza, BBC Russian, DW, Медиазона, Холод, SOTA, etc.), uses an LLM to classify and
deduplicate stories, and emits a compact Telegram-ready digest on stdout.

## Prerequisites

- Python 3.11+
- An [ollama.com](https://ollama.com) API key for cloud-hosted LLM inference

## Quickstart

```bash
make install              # install runtime + dev dependencies
cp .env.example .env      # then add your OLLAMA_API_KEY
make run                  # scrape, classify, assemble, format → stdout
```

## Pipeline

```
RSS sources → scrape → split into chunks → LLM classify → cross-chunk dedup
            → assemble digest → Telegram-formatted text on stdout
```

The orchestrator is `run.sh`; per-step exit codes are recorded in
`data/pipeline_status.json` for monitoring.

## Common tasks

```bash
make test          # pytest
make lint          # ruff
make health        # health-check report as JSON
make clean         # wipe data/ artifacts
./run.sh           # full pipeline
```

## Configuration

All tunables live in [`config.yaml`](config.yaml) — LLM model, parallelism,
date window, dedup thresholds, categories. The schema is validated at startup;
typos and missing keys fail fast with a clear message.

## Architecture

See [`CLAUDE.md`](CLAUDE.md) for the full architecture overview, directory
layout, and per-script responsibilities.
