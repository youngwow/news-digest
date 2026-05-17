.PHONY: install test lint format run health clean

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

clean:
	rm -rf data/chunks data/*.json data/*.md data/*.log
