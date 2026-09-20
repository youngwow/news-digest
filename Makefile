.PHONY: install test test-quick lint format \
        seed collect watch process serve run watchdog digest deliveries dataset quality \
        tg-login tg-status docker-up unlock

install:
	uv sync

test:
	uv run pytest -q

test-quick:
	uv run pytest -q -m "not slow"

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests

# ── сбор → обработка → лента ────────────────────────────────────────────────

seed:
	uv run python -m src sources seed

collect:
	uv run python -m src collect

watch:
	uv run python -m src collect --watch

process:
	uv run python -m src process

serve:
	uv run python -m src serve

# ── дайджест и эксплуатация ─────────────────────────────────────────────────

run:            # cron: сбор → обработка → дайджест (Telegram при telegram.deliver, иначе stdout)
	./run.sh

watchdog:       # cron: тишина, пока всё хорошо; ALERT (+ Telegram при telegram.alerts) иначе
	@./pipeline_check.sh

digest:         # Telegram-текст дайджеста на stdout; `make digest ARGS="--send"` — в чат
	@uv run python -m src digest --format telegram $(ARGS)

deliveries:
	@uv run python -m src deliveries

dataset:
	@uv run python -m src dataset $(ARGS)

quality:
	uv run python -m src quality --gold

tg-login:
	uv run python -m src telegram login

tg-status:
	uv run python -m src telegram status

docker-up:
	docker compose up --build

unlock:
	@rmdir data/.run.lock 2>/dev/null && echo "lock released" || echo "no lock to release"
