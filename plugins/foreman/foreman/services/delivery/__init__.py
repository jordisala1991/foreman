"""Delivery: a task in review. When a responsible agent says `foreman done`, the loop checks its branch (tests in the worktree,
the paths it touches, conflicts with main) and scores the merge risk: low, medium or high, with the reasons.
The project's autonomy decides which risks merge without the owner: `manual` (none: the default), `low`, or
`medium`. High never merges alone. Everything else waits for the owner's accept in the panel (`foreman accept`), or is
rejected: it goes back to its agent with his note (`foreman reject`).

Accepting only records the decision; the merge step (`merge_accepted`, through the project's provider, foreman/providers/) merges,
and the task closes once the merge is seen in main. The local provider merges into the main checkout (`squash` by
default, or `merge` = `--no-ff`); nothing is ever pushed or released."""
