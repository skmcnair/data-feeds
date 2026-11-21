#!/usr/bin/env bash
# Run engine commands on the latest main, then commit and push the results.
# If someone else pushed first, start again from their commit (the engine's
# steps are safe to repeat), up to three times.
#
#   bash engine/run_and_push.sh "<commit message>" "<commands>"
set -uo pipefail
msg="$1"
cmds="$2"
rc=0
for attempt in 1 2 3; do
  git fetch -q origin main && git reset -q --hard origin/main
  bash -c "$cmds"; rc=$?
  git add -A feeds shared
  if git diff --cached --quiet; then
    echo "nothing changed"
    exit $rc
  fi
  git -c user.name="data-feeds bot" \
      -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      commit -q -m "$msg"
  if git push -q origin HEAD:main; then
    echo "pushed: $msg"
    exit $rc
  fi
  echo "push rejected (attempt $attempt); retrying on the latest main"
  sleep $((attempt * 7))
done
echo "could not push after 3 attempts"
exit 1
