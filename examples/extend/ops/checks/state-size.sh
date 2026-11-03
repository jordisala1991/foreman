#!/bin/bash
# A [loop] checks command (docs/extending.md, docs/config.md «[loop] checks»): the size of this product's foreman
# state folder. Below STATE_INFO_MB it says nothing; above it, a notice that only informs; at STATE_ACT_MB or more,
# one that asks for action. It reads what foreman gives every command (FOREMAN_STATE, FOREMAN_PROJECT) instead of
# parsing foreman.toml with defaults of its own.
#
# Exit 0 or 1 is normal; any other code (or taking longer than the point's 30 s) is a check_failed notice.
set -u

if [ "${FOREMAN_API:-0}" -lt 1 ] || [ -z "${FOREMAN_STATE:-}" ]; then
  echo "state-size.sh: run by foreman (FOREMAN_API >= 1)" >&2
  exit 2
fi
info_mb=${STATE_INFO_MB:-500}
act_mb=${STATE_ACT_MB:-2000}
[ -d "$FOREMAN_STATE" ] || exit 0
mb=$(( $(du -sk "$FOREMAN_STATE" | cut -f1) / 1024 ))
[ "$mb" -lt "$info_mb" ] && exit 0
act=false
[ "$mb" -ge "$act_mb" ] && act=true
# the key names the case, so the same notice is not new on every pass while only the size changes
printf '{"kind": "state_size", "actionable": %s, "key": "state_size:%s", "text": "foreman state of %s is %s MB"}\n' \
  "$act" "$act" "$FOREMAN_PROJECT" "$mb"
