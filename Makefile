.PHONY: install hooks lint fmt test

install:
	uv sync

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
	uv run pytest
