# fm: project rules

- Before writing code, read the Architecture section of `DESIGN.md`: layers, ports, rules.
- Pure decisions (risk, autonomy, state rules) go in the core as functions; I/O stays in the loop and the services.
- No port without need: an external system or two real implementations.
- `make check` enforces it (tach layers, no `subprocess` in the core, no more `noqa` / `type: ignore` than `foreman-next`). Loosening a rule is the owner's call.
