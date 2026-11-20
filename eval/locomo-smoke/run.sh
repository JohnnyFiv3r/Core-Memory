#!/usr/bin/env bash
# Runs one LoCoMo conversation through the engine with live models, then
# writes report.md. It spends real money, within the budgets in
# core-memory.toml. See README.md.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
out=${1:?usage: run.sh <output-folder>}
mkdir -p "$out"
out=$(cd "$out" && pwd)

: "${CORE_MEMORY_DATABASE_URL:?set it to an empty PostgreSQL database the test may fill}"
if [ -z "${CORE_MEMORY_ANTHROPIC_KEY:-}" ]; then
  echo "run.sh: CORE_MEMORY_ANTHROPIC_KEY must hold an Anthropic API key" >&2
  exit 2
fi
bin=${CORE_MEMORY_BIN:-core-memory}
sample=${LOCOMO_SAMPLE:-conv-26}
max_minutes=${MAX_MINUTES:-120}
# Seconds a run waits before it looks again when nothing was due, and how
# many such runs in a row end the test.
pause=${PAUSE_SECONDS:-30}
quiet_limit=${QUIET_RUNS:-20}

# The budgets are per UTC day. A test that crossed midnight could spend two
# days' worth, so it starts only with enough of the day left.
left_in_day=$((86400 - $(date -u +%s) % 86400))
if [ "$left_in_day" -lt $(((max_minutes + 30) * 60)) ]; then
  echo "run.sh: less than $((max_minutes + 30)) minutes are left in the UTC day; start after midnight UTC" >&2
  exit 2
fi

data="$out/locomo10.json"
if [ ! -f "$data" ]; then
  curl -fsSL --retry 3 -o "$data" \
    https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json
fi
# The file this test was written against. A changed upstream file stops it.
echo "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4  $data" |
  sha256sum -c --quiet -
python3 -I "$here/locomo_to_events.py" "$data" "$sample" locomo "$out/events.jsonl"

config="$out/core-memory.toml"
cp "$here/core-memory.toml" "$config"
# READER_EFFORT, JUDGE_MODEL and JUDGE_EFFORT change the bindings, to
# compare models on the same conversation. See configure.py.
settings=()
[ -n "${READER_EFFORT:-}" ] && settings+=(--reader-effort "$READER_EFFORT")
[ -n "${JUDGE_MODEL:-}" ] && settings+=(--judge-model "$JUDGE_MODEL")
[ -n "${JUDGE_EFFORT:-}" ] && settings+=(--judge-effort "$JUDGE_EFFORT")
python3 -I "$here/configure.py" "$config" ${settings[@]+"${settings[@]}"}
if [ -n "${MODELS_CA_FILE:-}" ]; then
  printf '\n[models]\nca_file = "%s"\n' "$MODELS_CA_FILE" >>"$config"
fi
cm() { "$bin" --config "$config" "$@"; }

cm check >"$out/check.jsonl"
cm migrate
cm scope add locomo --workspace smoke
# One allowance covers both bindings: they send to the same destination.
cm scope allow locomo --binding reader
cm ingest --input "$out/events.jsonl" >"$out/ingest.jsonl"

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
  # Calls made, work still to do (ready or leased), and whether no call
  # can succeed: the API turned a binding down (1), or the server's
  # certificate was not trusted (2).
  read -r calls owed rejected < <(python3 -I "$here/report.py" --left \
    "$out/last-run.log" "$out/last-rounds.jsonl")
  if [ "$owed" -eq 0 ]; then
    why="everything was read and judged"
    break
  fi
  if [ "$rejected" -eq 1 ]; then
    why="the API turned the key or a model down; see runs.log"
    break
  fi
  if [ "$rejected" -eq 2 ]; then
    why="the model server's TLS certificate was not trusted, so nothing was sent: \
set MODELS_CA_FILE to the certificate authority of the proxy or gateway that re-signs it"
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
python3 -I "$here/report.py" "$out" "$why" >"$out/report.md"
cat "$out/report.md"
