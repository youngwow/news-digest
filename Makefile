.PHONY: install test lint format run health show show-list history alerts unlock tune tune-apply clean

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

show:
	@python3 src/show_digest.py

show-list:
	@python3 src/show_digest.py --list

history:
	@python3 src/show_history.py

tune:
	@python3 src/auto_reweight.py

tune-apply:
	@python3 src/auto_reweight.py --apply

alerts:
	@bash pipeline_check.sh; echo "---"; \
		tail -20 data/alerts.log 2>/dev/null || echo "(no alerts logged yet)"

unlock:
	@rmdir data/.run.lock 2>/dev/null && echo "lock released" || echo "no lock to release"

clean:
	rm -rf data/chunks data/*.json data/*.md data/*.log
