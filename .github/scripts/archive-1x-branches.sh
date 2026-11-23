#!/usr/bin/env bash
# Archives Core Memory's old branches under one tag, then deletes them.
#
# Every branch except the ones kept (KEEP, plus any with an open pull
# request) becomes a parent of one commit with an empty tree. The tag
# ARCHIVE_TAG points at that commit, so every commit of every archived
# branch stays reachable, and the tag's message lists each branch with its
# tip. A branch comes back with:
#
#   git fetch origin tag archive/1.x-branches
#   git branch <name> <tip from the tag's message>
#
# Nothing is deleted until the tag is on the remote and every tip is
# checked to be reachable from it. With DRY_RUN=true nothing is written.
#
# Inputs (environment): DRY_RUN (true/false), KEEP (space-separated branch
# names), ARCHIVE_TAG, REMOTE (default origin). `gh` must be able to list
# the repository's open pull requests.
set -euo pipefail

remote="${REMOTE:-origin}"
tag="${ARCHIVE_TAG:?ARCHIVE_TAG is required}"
dry_run="${DRY_RUN:-true}"
work="$(mktemp -d)"

git fetch --quiet --prune --no-tags "$remote" "+refs/heads/*:refs/remotes/$remote/*"

# Branches with an open pull request are kept, whatever KEEP says.
open_prs="$(gh pr list --state open --limit 500 --json headRefName --jq '.[].headRefName')"
keep=" ${KEEP:-} $(echo "$open_prs" | tr '\n' ' ') "

: > "$work/index"
while read -r name sha date; do
  case "$keep" in *" $name "*) continue ;; esac
  printf '%s %s %s\n' "$name" "$sha" "$date" >> "$work/index"
done < <(git for-each-ref --format='%(refname:lstrip=3) %(objectname) %(committerdate:short)' \
           "refs/remotes/$remote" | grep -v '^HEAD ')

count="$(wc -l < "$work/index" | tr -d ' ')"
echo "Keeping:$keep"
echo "Branches to archive and delete: $count"
if [ "$count" -eq 0 ]; then
  echo "Nothing to do."
  exit 0
fi

# master is deleted only when v1.x holds every commit of it.
if grep -q '^master ' "$work/index"; then
  if ! git merge-base --is-ancestor "refs/remotes/$remote/master" "refs/remotes/$remote/v1.x"; then
    echo "::error::master has commits that v1.x does not; refusing to delete it."
    exit 1
  fi
fi

{
  echo "Archive of Core Memory's old branches"
  echo
  echo "Each line is a branch, its tip, and the tip's date. Every tip is kept"
  echo "reachable from this tag. A line does not show whether the branch was deleted."
  echo "To restore one: git fetch origin tag $tag && git branch <name> <tip>"
  echo
  cat "$work/index"
} > "$work/message"

if git ls-remote --exit-code --tags "$remote" "refs/tags/$tag" > /dev/null; then
  # A run that stopped after pushing the tag: use the tag it pushed.
  git fetch --quiet "$remote" "+refs/tags/$tag:refs/tags/$tag"
  archive="$(git rev-parse "refs/tags/$tag^{commit}")"
  echo "Using the existing $tag at $archive."
  # Only a branch still at the tip the tag lists for it is deleted: restoring
  # from the tag must bring back what was deleted. One that moved since, or
  # that the tag does not list, is left, even when its tip is reachable.
  git tag -l --format='%(contents)' "$tag" |
    awk 'NF == 3 && $2 ~ /^[0-9a-f]+$/ {print $1, $2}' > "$work/listed"
  : > "$work/held"
  : > "$work/moved"
  while read -r name sha date; do
    if grep -qxF "$name $sha" "$work/listed"; then
      printf '%s %s %s\n' "$name" "$sha" "$date" >> "$work/held"
    else
      echo "$name" >> "$work/moved"
    fi
  done < "$work/index"
  mv "$work/held" "$work/index"
  count="$(wc -l < "$work/index" | tr -d ' ')"
  if [ -s "$work/moved" ]; then
    echo "::error::These branches are not at the tip $tag lists for them, so they are left:"
    cat "$work/moved"
    if [ "$count" -eq 0 ]; then
      exit 1
    fi
  fi
else
  empty_tree="$(git hash-object -t tree /dev/null)"
  parents=()
  while read -r sha; do parents+=(-p "$sha"); done < <(awk '{print $2}' "$work/index" | sort -u)
  archive="$(git commit-tree "$empty_tree" "${parents[@]}" -F "$work/message")"
fi

# Every tip must be reachable from the archive before anything is deleted.
missing=0
while read -r name sha _; do
  if ! git merge-base --is-ancestor "$sha" "$archive"; then
    echo "::error::$name ($sha) is not reachable from the archive."
    missing=$((missing + 1))
  fi
done < "$work/index"
if [ "$missing" -ne 0 ]; then
  echo "::error::$missing branches are not in the archive; nothing was deleted."
  exit 1
fi
echo "Every one of the $count tips is reachable from the archive commit $archive."

if [ "$dry_run" = "true" ]; then
  echo
  echo "Dry run: nothing was written. These branches would be archived and deleted:"
  cat "$work/index"
  exit 0
fi

if ! git ls-remote --exit-code --tags "$remote" "refs/tags/$tag" > /dev/null; then
  git tag -a "$tag" "$archive" -F "$work/message"
  git push --quiet "$remote" "refs/tags/$tag"
fi
pushed="$(git ls-remote "$remote" "refs/tags/$tag^{}" | awk '{print $1}')"
if [ "$pushed" != "$archive" ]; then
  echo "::error::$tag on the remote is $pushed, not $archive; nothing was deleted."
  exit 1
fi
echo "Pushed $tag."

# Each push names the branch by its full ref (a tag may share its name) and
# deletes it only while it is still at the tip that was archived: a branch
# that moved since is left, and the count below says so.
mapfile -t lines < "$work/index"
for ((i = 0; i < ${#lines[@]}; i += 50)); do
  batch=()
  for line in "${lines[@]:i:50}"; do
    read -r name sha _ <<< "$line"
    batch+=("--force-with-lease=refs/heads/$name:$sha" ":refs/heads/$name")
  done
  # A batch is deleted whole or not at all; one that fails is tried a
  # branch at a time, so one protected branch does not keep the rest.
  if ! git push --quiet --atomic "$remote" "${batch[@]}"; then
    for ((j = 0; j < ${#batch[@]}; j += 2)); do
      git push --quiet "$remote" "${batch[@]:j:2}" || echo "::warning::could not delete ${batch[j+1]#:refs/heads/}"
    done
  fi
done
# What the remote has now is the count, not what the pushes said.
git ls-remote --heads "$remote" | awk '{sub("refs/heads/", "", $2); print $2}' | sort > "$work/remaining"
awk '{print $1}' "$work/index" | sort > "$work/archived"
left="$(comm -12 "$work/archived" "$work/remaining")"
deleted="$(comm -23 "$work/archived" "$work/remaining" | wc -l | tr -d ' ')"
echo "Deleted $deleted of $count branches."
if [ -n "$left" ]; then
  echo "::error::These archived branches could not be deleted:"
  echo "$left"
  exit 1
fi
echo "Branches left: $(tr '\n' ' ' < "$work/remaining")"
# A branch left because it moved is not a success.
if [ -s "$work/moved" ]; then
  exit 1
fi
