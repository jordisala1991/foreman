"""foreman's developer tools (dev/foreman-dev): measuring what foreman's sessions spend and fill, and its history as a
timeline. Not part of the product: no foreman command, hook, skill or doc of the product names them, and nothing in
lib/foreman imports this package. They only read, through lib/foreman's own readers (config, transcripts, identity,
orchestrator, i18n and the history collector, timeline.py)."""
