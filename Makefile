.PHONY: install sync hooks lint fmt test uninstall

sync:
	uv sync

install: sync
	uv tool install --editable --force .

uninstall:
	uv tool uninstall omniscience

hooks:
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run basedpyright

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest -n auto --cov --cov-report=term-missing --cov-report=html
