.PHONY: install test test-quick lint format \
        seed collect watch tg-login tg-status process quality serve docker-up happy-pr happy-gr \
        digest digest-resume digest-health digest-watchdog digest-show digest-show-list digest-history \
        digest-weekly digest-usage digest-dataset-stats digest-tune digest-tune-apply digest-alerts \
        digest-unlock digest-clean

# ── Общее ────────────────────────────────────────────────────────────────────

install:
	uv sync

test:
	uv run pytest -q

test-quick:
	uv run pytest -q -m "not slow"

lint:
	uv run ruff check src news_digest tests

format:
	uv run ruff format src news_digest tests

# ── Платформа: сбор, обработка, лента, API (src/) ────────────────────────────

seed:
	uv run python -m src sources seed

collect:
	uv run python -m src collect

watch:
	uv run python -m src collect --watch

tg-login:
	uv run python -m src telegram login

tg-status:
	uv run python -m src telegram status

happy-pr:
	bash scripts/happy_path_pr.sh

happy-gr:
	bash scripts/happy_path_gr.sh

process:
	uv run python -m src process

quality:
	uv run python -m src quality --gold

serve:
	uv run python -m src serve

docker-up:
	docker compose up --build

# ── Telegram-дайджест: RSS → LLM-классификация → дайджест (news_digest/) ─────

digest:
	./run.sh

digest-resume:
	./run.sh --resume

digest-health:
	@uv run python -m news_digest health

digest-watchdog:
	@uv run python -m news_digest watchdog

digest-show:
	@uv run python -m news_digest show

digest-show-list:
	@uv run python -m news_digest show --list

digest-history:
	@uv run python -m news_digest history

digest-weekly:
	@uv run python -m news_digest weekly

digest-usage:
	@uv run python -m news_digest usage

digest-dataset-stats:
	@uv run python -m news_digest dataset-stats

digest-tune:
	@uv run python -m news_digest tune

digest-tune-apply:
	@uv run python -m news_digest tune --apply

digest-alerts:
	@bash pipeline_check.sh; echo "---"; \
		tail -20 data/alerts.log 2>/dev/null || echo "(no alerts logged yet)"

digest-unlock:
	@rmdir data/.run.lock 2>/dev/null && echo "lock released" || echo "no lock to release"

digest-clean:  # артефакты дайджеста; hub.db и сессии Telegram не трогает
	rm -rf data/chunks data/*.json data/*.md data/*.log
