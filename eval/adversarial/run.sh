#!/usr/bin/env bash
# Runs the hard cases in cases.toml through the engine with live models, as
# the LoCoMo smoke test does and with its config, then scores what was
# stored. It spends real money, within the budgets in
# ../locomo-smoke/core-memory.toml. See README.md.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
smoke="$here/../locomo-smoke"
out=${1:?usage: run.sh <output-folder>}
mkdir -p "$out"
out=$(cd "$out" && pwd)

: "${CORE_MEMORY_DATABASE_URL:?set it to an empty PostgreSQL database the test may fill}"
if [ -z "${CORE_MEMORY_ANTHROPIC_KEY:-}" ]; then
  echo "run.sh: CORE_MEMORY_ANTHROPIC_KEY must hold an Anthropic API key" >&2
  exit 2
fi
bin=${CORE_MEMORY_BIN:-core-memory}
max_minutes=${MAX_MINUTES:-60}
# Independent runs of the cases, each in a scope and workspace of its own, so
# no run's beads are judged with another's. The judge's budget of a day holds
# five.
repeats=${REPEATS:-1}
case "$repeats" in
  [1-5]) ;;
  *) echo "run.sh: REPEATS must be 1 to 5" >&2; exit 2 ;;
esac
pause=${PAUSE_SECONDS:-30}
quiet_limit=${QUIET_RUNS:-20}

# The budgets are per UTC day; see ../locomo-smoke/run.sh.
left_in_day=$((86400 - $(date -u +%s) % 86400))
if [ "$left_in_day" -lt $(((max_minutes + 30) * 60)) ]; then
  echo "run.sh: less than $((max_minutes + 30)) minutes are left in the UTC day; start after midnight UTC" >&2
  exit 2
fi

config="$out/core-memory.toml"
cp "$smoke/core-memory.toml" "$config"
if [ -n "${MODELS_CA_FILE:-}" ]; then
  printf '\n[models]\nca_file = "%s"\n' "$MODELS_CA_FILE" >>"$config"
fi
cm() { "$bin" --config "$config" "$@"; }

cm check >"$out/check.jsonl"
cm migrate
: >"$out/ingest.jsonl"
for i in $(seq 1 "$repeats"); do
  scope="adversarial-$i"
  python3 -I "$here/cases_to_events.py" "$here/cases.toml" "$scope" "$out/events-$i.jsonl"
  cm scope add "$scope" --workspace "$scope"
  # One allowance covers both bindings: they send to the same destination.
  cm scope allow "$scope" --binding reader
  cm ingest --input "$out/events-$i.jsonl" >>"$out/ingest.jsonl"
done

: >"$out/rounds.jsonl"
: >"$out/runs.log"
deadline=$(($(date -u +%s) + max_minutes * 60))
quiet=0
why=""
while :; do
  if ! cm read run >"$out/last-rounds.jsonl" 2>"$out/last-run.log"; then
    echo "run.sh: read run failed; see $out/last-run.log" >&2
  fi
  cat "$out/last-rounds.jsonl" >>"$out/rounds.jsonl"
  cat "$out/last-run.log" >>"$out/runs.log"
  read -r calls owed rejected < <(python3 -I "$smoke/report.py" --left \
    "$out/last-run.log" "$out/last-rounds.jsonl")
  if [ "$owed" -eq 0 ]; then
    why="everything was read and judged"
    break
  fi
  if [ "$rejected" -eq 1 ]; then
    why="the API turned the key or a model down; see runs.log"
    break
  fi
  if [ "$calls" -eq 0 ]; then
    quiet=$((quiet + 1))
  else
    quiet=0
  fi
  if [ "$quiet" -ge "$quiet_limit" ]; then
    why="nothing was due for $((quiet_limit * pause)) seconds"
    break
  fi
  if [ "$(date -u +%s)" -ge "$deadline" ]; then
    why="the time limit of $max_minutes minutes"
    break
  fi
  if [ "$calls" -eq 0 ]; then
    sleep "$pause"
  fi
done

cm read status >"$out/status.jsonl"
cm export >"$out/export.jsonl"
python3 -I "$smoke/report.py" "$out" "$why" >"$out/report.md"
python3 -I "$here/score.py" "$here/cases.toml" "$out/export.jsonl" >"$out/score.md"
cat "$out/score.md"
echo
cat "$out/report.md"
