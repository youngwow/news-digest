.PHONY: install test lint format run health alerts unlock clean

install:
	pip install -r requirements.txt
	pip install -e ".[dev]"

test:
	pytest -v

lint:
	ruff check src tests

format:
	ruff format src tests

run:
	./run.sh

health:
	python3 src/health_check.py --json | python3 -m json.tool

alerts:
	@bash pipeline_check.sh; echo "---"; \
		tail -20 data/alerts.log 2>/dev/null || echo "(no alerts logged yet)"

unlock:
	@rmdir data/.run.lock 2>/dev/null && echo "lock released" || echo "no lock to release"

clean:
	rm -rf data/chunks data/*.json data/*.md data/*.log
