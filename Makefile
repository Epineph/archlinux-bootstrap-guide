.PHONY: check lint test

check: lint test

lint:
	@for script in bootstrap.sh scripts/*.sh; do bash -n "$$script"; done
	shellcheck bootstrap.sh scripts/*.sh
	shfmt -d -i 2 bootstrap.sh scripts/*.sh
	ruff check lib scripts/bootstrap.py tests
	ruff format --check lib scripts/bootstrap.py tests

test:
	python3 -m unittest discover -s tests -v
