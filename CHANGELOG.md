# Changelog

All notable changes to foreman are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the version follows [Semantic Versioning](https://semver.org/). The version lives in `.claude-plugin/plugin.json`;
each release is tagged `foreman--vX.Y.Z` and the `release` branch points at the latest one (CONTRIBUTING.md).

Besides the usual sections, a release may have **Config** (new `foreman.toml` keys and the `config_schema` they need)
and **Upgrade notes** (anything a user has to do: state, backlog, service or registry changes).

## [Unreleased]

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
