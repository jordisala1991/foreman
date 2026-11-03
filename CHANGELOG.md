# Changelog

All notable changes to foreman are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the version follows [Semantic Versioning](https://semver.org/). The version lives in `.claude-plugin/plugin.json`;
each release is tagged `foreman--vX.Y.Z` and the `release` branch points at the latest one (CONTRIBUTING.md).

Besides the usual sections, a release may have **Config** (new `foreman.toml` keys and the `config_schema` they need)
and **Upgrade notes** (anything a user has to do: state, backlog, service or registry changes).

## [Unreleased]

### Added

- Machine settings: a `[machine]` section in the registry (`~/.config/foreman/projects.toml`), shaped like a
  foreman.toml (`[machine.project]`, `[machine.agents]`, `[machine.loop]`, `[machine.deny]`,
  `[machine.deny.grants]`, `[machine.strings]`), that every registered product inherits when it loads: what holds
  for every product (the owner, the language, the commands nobody runs without the owner) is written once. A
  product's scalar wins, lists add up, tables merge key by key. A bad value there is left out alone, with a warning
  in every product. `foreman check` says how many settings and deny rules a product inherits;
  `foreman config --json` gives `registry` (or `registry+foreman.toml` for a list both add to) as their source.
- `foreman permissions` says where each rule comes from: it marks the rules the registry's `[machine.deny]` gives
  («from the machine», «machine and product»), counts them, and lists the inherited rules the product removes with
  `[deny] drop`. `--json` adds `from` to each rule (`foreman`, `machine`, `product` or `both`) and `inherited`
  (`{rules, dropped}`).
- `foreman permissions --machine-candidate FILE`: the effect of a changed registry `[machine]` on every registered
  product, read only: the inherited rules before and after, the settings that change, each product's candidate test
  (rules, table rows, failing rows, the lifeline, what opens), the warnings it would gain, and the rows that would
  fall in two or more `[deny.grants]` groups other than before (each needs its own yes, while the row's verdict stays
  the same). Exit 1 when a product loses a row or a lifeline command, gets a warning or does not load.

### Changed

- The guard sees every tool, not only Bash and the write tools: its hook runs before any tool call (no matcher in
  `hooks/classic.json`). With one rule and no list of tools, a path or URL a call names (Read's `file_path`, Grep's
  `path`, WebFetch's `url`, a plugin's `targetPath`…) gets the verdict `cat PATH` would get in Bash, and a command
  it carries (Monitor's `command`) the verdict of that command; free text (what a Write writes, a prompt, a message)
  is never compared. Writing a secret with Write or Edit is refused as reading it. Guard tables accept
  `tool:NAME JSON` rows; `guard.jsonl` and `approved.jsonl` record the tool and the place or command that matched,
  never the rest of the input. Without a Python foreman can use, every tool call is refused while a registry exists
  (before, only Bash and the write tools were). Cost: one more hook process per call of the other tools, about
  40 ms each.
- The orchestrator is asked for the owner's yes in the chat for any tool, not only Bash: a Read, WebFetch, Monitor
  or any other call of its main loop that names a place or a command of a `[deny.grants]` group gets the same
  question a Bash command of the group gets (before, it was refused with no question). The question names the tool
  and what matched; the answer's record in `approved.jsonl` carries `tool`. In a project with groups, every call of
  another tool than Bash in a session's main loop costs one more `foreman hook mod grant` process, as a Bash call
  already did.

### Config

- Schema 19: `[deny] drop`, a list of rules inherited from the registry's `[machine.deny]` that this product removes
  (a pattern as written there, or `"group:NAME"` for a whole group). `sources` of `foreman config --json` may say
  `registry+foreman.toml` (additive: `api` stays 1).

### Upgrade notes

- Nothing to do: without a `[machine]` in the registry every product loads as before (`foreman check` adds «inherited
  from the machine (registry [machine]): 0 settings, 0 deny rules», `foreman permissions` «Rules from the
  machine (the registry's [machine.deny]): 0»). An older foreman ignores `[machine]` with
  «unknown section [machine]»: before moving a rule from the products' foreman.toml to `[machine]`, make sure every
  session runs a foreman that knows it (`foreman session --all --behind`); until then keep it in both places.

## [0.56.0] - 2026-11-03

### Added

- First public release of foreman.

### Changed

- In a repo with `public = true`, `foreman integrate` also scans the branch's commit messages (`main..BRANCH`) with
  the checks the PR title and body pass (no internal IDs, then `privacy_scan`), before the local CI and before
  anything is pushed: a public PR shows them forever, even when the merge squashes them. A hit stops with «nothing
  pushed» and names the commit and the rule, never the matched text.
- The test suite covers a whole loop pass (the hook's tick and `loop.jsonl`) when all the ready work waits for a
  pending decision: the WIP notice is information only, says which decision each issue waits for, and the pass is
  skipped; one ready issue that can be launched next to them sends the pass to the model. No behaviour changes: a
  ready issue whose remaining sub-issues are measurements (`kind = "measure"`) still counts as launchable, since an
  agent can run them.

## [0.55.0]

The version the public history starts from; the changes before it were not published.
