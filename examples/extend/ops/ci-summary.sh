#!/bin/bash
# A [repos.*] ci_summary command (docs/extending.md, docs/config.md «local CI»): one run of the local CI's output
# on stdin, one line on stdout for the PR comment. It understands unittest («Ran N tests», «OK», «FAILED (…)») and
# pytest («N passed, M failed in …»); anything else is «no test summary». FOREMAN_CI is the run's value of ci_runs.
set -u

summary=$(grep -E '^(Ran [0-9]+ tests? in|OK( \(.*\))?$|FAILED \(.*\)$)|[0-9]+ (passed|failed)' \
  | sed -E 's/^=+ //; s/ =+$//' | tr '\n' ' ' | sed -E 's/ +$//')
printf '%s: %s\n' "${FOREMAN_CI:-default run}" "${summary:-no test summary}"
