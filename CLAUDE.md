# fm: project rules

Each rule ends with who enforces it: `(make check)` fails the build; `(reviewer)` is checked in review.

- Before writing code, read the Architecture section of `DESIGN.md`: layers, packages, where new code goes. (reviewer)
- A module sits in the package of its layer and imports its own layer or below; core areas import only their `depends_on`. (make check: tach)
- Only `fm/proc.py` runs programs; `lock.py` is the one exception. (make check: ruff TID251)
- Only provider adapters talk to the platform (`git push`, `gh`); services orchestrate through the port. (reviewer)
- Retrying and blocking a failed platform call lives only in `loop.guarded`. (reviewer)
- Pure decisions are functions in `core/`; `decide` imports only `risk` and runs no file or program. (make check: tach + `tests/test_decide.py`)
- No port without an external system or two real implementations. (reviewer)
- Data from outside is typed at the border; no dict crosses the port. (reviewer)
- Modules stay at most 300 lines; a grown module is split along its area's seams, never squeezed. (make check: `make size`)
- No suppressions: `ruff --ignore-noqa`, pyright ignore comments off, `make suppressions`. (make check)
- Use `GLOSSARY.md`'s terms, one name per concept; it changes only by the owner's decision. (reviewer; `tests/test_no_old_names.py` covers old names)
- Loosening a rule (`tach.toml`, ruff ignores, `pyproject.toml`, `suppressions`) is the owner's call. (reviewer)
