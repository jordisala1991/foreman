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
# no tool covers pyright:, pylint:, fmt:, isort: and yapf: directive comments (ruff honours the last three, with or without a ruff:
# prefix, and --ignore-noqa does not cover them), so none of any kind is allowed, in plugins/ and tests/. A suppression is not a fix.
suppressions:
	@! git grep -nE '#[[:space:]]*(ruff:[[:space:]]*)?(pyright|pylint|fmt|isort|yapf):' -- plugins tests || { echo "suppressions: fix the cause, do not silence it"; exit 1; }

# File length: ruff has no rule for it. Python under plugins/ 300 lines (limit in pyproject.toml), tests 600. Given a package,
# pylint walks only its subpackages, so plugins/ goes as a file list; tests/ is no package, so pylint walks all of it.
# bin/foreman has no .py suffix, so find skips it: it is named.
# Directory entries: a directory under plugins/foreman/foreman (root included) holds at most DIR_LIMIT entries, .py modules except
# __init__.py plus subdirectories except __pycache__. A directory in DIR_CEILINGS (dir=count) may not exceed its own count instead.
DIR_LIMIT = 15
DIR_CEILINGS = plugins/foreman/foreman/core/tasks=18

size:
	uv run pylint $$(find plugins -name '*.py') plugins/foreman/bin/foreman
	uv run pylint --max-module-lines=600 tests
	@bad=0; for d in $$(find plugins/foreman/foreman -type d ! -name __pycache__); do \
		n=$$(find "$$d" -mindepth 1 -maxdepth 1 \( -type f -name '*.py' ! -name __init__.py -o -type d ! -name __pycache__ \) | wc -l | tr -d ' '); \
		allowed=$(DIR_LIMIT); \
		for c in $(DIR_CEILINGS); do [ "$$d" = "$${c%=*}" ] && allowed=$${c#*=}; done; \
		[ "$$n" -le "$$allowed" ] || { echo "size: $$d has $$n entries, at most $$allowed"; bad=1; }; \
	done; exit $$bad

# -W error::ResourceWarning: a file or pipe left open is reported loudly (traceback of where it was opened), not swallowed.
# -n auto: one worker per core (pytest-xdist); `test-serial` runs them in one process. pytest puts plugins/foreman (pythonpath in
# pyproject.toml), so foreman, on the path, and tests/, so `helpers`.
test:
	uv run python -m pytest -q -n auto --durations=10 -W error::ResourceWarning tests

test-serial:
	uv run python -m pytest -q --durations=10 -W error::ResourceWarning tests
