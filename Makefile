# One entry point for the code checks (CI runs `make check`). Dev tools (ruff, pyright, pylint) are the `dev` dependency group in
# pyproject.toml, locked in uv.lock, and run through `uv run`; tsc runs through npx. Nothing is added to fm's runtime.
# To change a tool's version: edit pyproject.toml, then `uv lock`.
# Fail if uv.lock is out of date with pyproject.toml; never rewrite it silently.
export UV_LOCKED := 1

.PHONY: check fix lint format types size panel test

# Everything, in this order, stopping at the first failure (even under `make -j`).
check:
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory format
	@$(MAKE) --no-print-directory types
	@$(MAKE) --no-print-directory size
	@$(MAKE) --no-print-directory panel
	@$(MAKE) --no-print-directory test
	@echo "make check: all passed"

# Fix what is mechanical (imports, formatting). What is left is a design signal: refactor, do not silence.
fix:
	uv run ruff check --fix .
	uv run ruff format .

# --ignore-noqa: a rule cannot be turned off with a comment.
lint:
	uv run ruff check --ignore-noqa .

format:
	uv run ruff format --check .

# Strict on fm/ (see [tool.pyright] in pyproject.toml).
types:
	uv run pyright

# File length: ruff has no rule for it. fm/ 300 lines (limit in pyproject.toml), tests 600.
size:
	uv run pylint fm
	uv run pylint --max-module-lines=600 tests

panel:
	@[ -d panel/node_modules ] || (cd panel && npm ci --silent --no-audit --no-fund)
	cd panel && npx tsc --noEmit

test:
	python3 -m unittest discover -s tests
