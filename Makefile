# One entry point for the code checks (CI runs `make check`). Dev tools run through uvx and npx; nothing is added to fm's runtime.
# Tool versions live here, in one place, pinned so a new release cannot turn the check red by itself.
RUFF    := uvx ruff@0.17.4
PYRIGHT := uvx pyright@1.1.414
PYLINT  := uvx pylint@4.1.3

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
	$(RUFF) check --fix .
	$(RUFF) format .

# --ignore-noqa: a rule cannot be turned off with a comment.
lint:
	$(RUFF) check --ignore-noqa .

format:
	$(RUFF) format --check .

# Strict on fm/ (see [tool.pyright] in pyproject.toml).
types:
	$(PYRIGHT)

# File length: ruff has no rule for it. fm/ 300 lines (limit in pyproject.toml), tests 600.
size:
	$(PYLINT) fm
	$(PYLINT) --max-module-lines=600 tests

panel:
	@[ -d panel/node_modules ] || (cd panel && npm ci --silent --no-audit --no-fund)
	cd panel && npx tsc --noEmit

test:
	python3 -m unittest discover -s tests
