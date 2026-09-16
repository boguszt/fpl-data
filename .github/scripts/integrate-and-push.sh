#!/usr/bin/env bash
# Merge origin/master into this job's commit, then push. Fail the job if the
# push is still rejected — a silent skip here is how the site goes stale.
set -euo pipefail

git fetch origin master

if ! git merge --no-edit origin/master; then
  echo "::error::Could not merge origin/master onto this job's commit. Push aborted so the stall is visible."
  exit 1
fi

if git push origin HEAD:master; then
  exit 0
fi

echo "push rejected; fetching and merging origin/master, then retrying"
git fetch origin master
if ! git merge --no-edit origin/master; then
  echo "::error::Push was rejected and a second merge of origin/master failed."
  exit 1
fi
if git push origin HEAD:master; then
  exit 0
fi

echo "::error::git push was rejected after merging origin/master. The scheduled update did not land on GitHub."
exit 1
