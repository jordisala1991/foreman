# fm: project rules

- Before writing code, read the Architecture section of `DESIGN.md`: layers, ports, rules.
- Pure decisions (risk, autonomy, state rules) go in the core as functions; I/O stays in the loop and the services.
- No port without need: an external system or two real implementations.
- `make check` enforces it (tach layers, no `subprocess` in the core, no suppressions at all: `ruff --ignore-noqa`, pyright with ignore comments off, `make suppressions`). Loosening a rule is the owner's call.
- Use the terms of `GLOSSARY.md`: one name per concept, the Spanish the owner uses, the words to avoid. It changes only by the owner's decision.
