.PHONY: install test lint format run health show show-list history weekly usage dataset-stats alerts unlock tune tune-apply clean

install:
	pip install -e ".[dev]"

test:
	pytest -v

lint:
	ruff check news_digest tests

format:
	ruff format news_digest tests

run:
	./run.sh

health:
	@python3 -m news_digest health

show:
	@python3 -m news_digest show

show-list:
	@python3 -m news_digest show --list

history:
	@python3 -m news_digest history

weekly:
	@python3 -m news_digest weekly

usage:
	@python3 -m news_digest usage

dataset-stats:
	@python3 -m news_digest dataset-stats

tune:
	@python3 -m news_digest tune

tune-apply:
	@python3 -m news_digest tune --apply

alerts:
	@bash pipeline_check.sh; echo "---"; \
		tail -20 data/alerts.log 2>/dev/null || echo "(no alerts logged yet)"

unlock:
	@rmdir data/.run.lock 2>/dev/null && echo "lock released" || echo "no lock to release"

clean:
	rm -rf data/chunks data/*.json data/*.md data/*.log
