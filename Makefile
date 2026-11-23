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

# plugins/foreman/foreman/ and tests/ are both strict (see [tool.pyright] in pyproject.toml and tests/pyright-strict.json).
types:
	uv run pyright
	uv run pyright -p tests/pyright-strict.json

# Architecture: layers and adapters (tach.toml, DESIGN.md "Architecture"). Process-running (subprocess, os.system…) is
# banned outside the allowed modules by ruff, in `make lint`.
arch:
	uv run tach check

# Suppression comments. noqa is ignored by `make lint` (--ignore-noqa) and type: ignore by pyright (enableTypeIgnoreComments = false);
# no tool covers pyright: and pylint: directive comments, so none of any kind is allowed. A suppression is not a fix.
suppressions:
	@! git grep -nE '#[[:space:]]*(pyright|pylint):' -- plugins/foreman/foreman tests || { echo "suppressions: fix the cause, do not silence it"; exit 1; }

# File length: ruff has no rule for it. plugins/foreman/foreman/ 300 lines (limit in pyproject.toml), tests 600. Given a package,
# pylint walks only its subpackages, so foreman/ goes as a file list; tests/ is no package, so pylint walks all of it.
size:
	uv run pylint $$(find plugins/foreman/foreman -name '*.py')
	uv run pylint --max-module-lines=600 tests

# -W error::ResourceWarning: a file or pipe left open is reported loudly (traceback of where it was opened), not swallowed.
# -n auto: one worker per core (pytest-xdist); `test-serial` runs them in one process. pytest puts plugins/foreman (pythonpath in
# pyproject.toml), so foreman, on the path, and tests/, so `helpers`.
test:
	uv run python -m pytest -q -n auto --durations=10 -W error::ResourceWarning tests

test-serial:
	uv run python -m pytest -q --durations=10 -W error::ResourceWarning tests
