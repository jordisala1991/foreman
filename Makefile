# One entry point for the code checks (CI runs `make check`). Dev tools (ruff, pyright, pylint, tach) are the `dev` dependency group in
# pyproject.toml, locked in uv.lock, and run through `uv run`. Nothing is added to fm's runtime.
# To change a tool's version: edit pyproject.toml, then `uv lock`.
# Fail if uv.lock is out of date with pyproject.toml; never rewrite it silently.
export UV_LOCKED := 1

.PHONY: check fix lint format types arch suppressions size test test-serial

# Everything, in this order, stopping at the first failure (even under `make -j`).
check:
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory format
	@$(MAKE) --no-print-directory types
	@$(MAKE) --no-print-directory arch
	@$(MAKE) --no-print-directory suppressions
	@$(MAKE) --no-print-directory size
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

# plugins/foreman/foreman/ strict (see [tool.pyright] in pyproject.toml); tests/ standard, strict for every test file not in
# tests/standard-only.txt (the ratchet: it only shrinks).
types:
	uv run pyright
	uv run pyright -p tests/pyright-standard.json
	@files=$$(find tests -name '*.py' | sort | grep -vxFf tests/standard-only.txt; [ $$? -le 1 ]) || exit 1; \
	[ -z "$$files" ] || uv run pyright -p tests/pyright-strict.json $$files

# Architecture: layers and adapters (tach.toml, DESIGN.md "Architecture"). Process-running (subprocess) is banned outside the
# allowed modules by ruff, in `make lint`.
arch:
	uv run tach check

# Suppression comments. noqa is ignored by `make lint` (--ignore-noqa) and type: ignore by pyright (enableTypeIgnoreComments = false);
# these are the ones no tool covers: none allowed. A suppression is not a fix.
suppressions:
	@! git grep -nE '#[[:space:]]*(pyright:[[:space:]]*(ignore|basic|off)|pylint:[[:space:]]*disable)' -- plugins/foreman/foreman tests || { echo "suppressions: fix the cause, do not silence it"; exit 1; }

# File length: ruff has no rule for it. plugins/foreman/foreman/ 300 lines (limit in pyproject.toml), tests 600.
size:
	uv run pylint plugins/foreman/foreman
	uv run pylint --max-module-lines=600 tests

# -W error::ResourceWarning: a file or pipe left open is reported loudly (traceback of where it was opened), not swallowed.
# -n auto: one worker per core (pytest-xdist); `test-serial` runs them in one process. pytest puts plugins/foreman (pythonpath in
# pyproject.toml), so foreman, on the path, and tests/, so `helpers`.
test:
	uv run python -m pytest -q -n auto --durations=10 -W error::ResourceWarning tests

test-serial:
	uv run python -m pytest -q --durations=10 -W error::ResourceWarning tests
