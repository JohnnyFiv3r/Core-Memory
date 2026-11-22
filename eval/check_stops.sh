#!/usr/bin/env bash
# Proves the "Stopped because ..." line of both eval runners against a
# stand-in engine: no PostgreSQL, no credentials, no network. Needs python3
# on PATH. Run it with: bash eval/check_stops.sh
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "check_stops.sh: python3 is needed on PATH" >&2
  exit 2
fi

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# The runners call only `date -u +%s`; a fixed noon keeps the check from
# failing in the last half hour of the UTC day.
mkdir "$tmp/bin"
printf '#!/usr/bin/env bash\necho 43200\n' >"$tmp/bin/date"
printf '#!/usr/bin/env bash\ncat >/dev/null\n' >"$tmp/bin/sha256sum"
printf '#!/usr/bin/env bash\necho "check_stops.sh: no network in this check" >&2\nexit 1\n' >"$tmp/bin/curl"
cat >"$tmp/bin/core-memory-standin" <<'STANDIN'
#!/usr/bin/env bash
if [ "${1:-}" = "--config" ]; then shift 2; fi
case "${1:-} ${2:-}" in
  "check "*)
    echo '{"binding":"reader","model":"claude-haiku-5-5"}'
    echo '{"reader":{"binding":"reader"}}' ;;
  "read run")
    printf '%s' "${STANDIN_ROUND:-}"
    printf '%s\n' "${STANDIN_TOTALS:-}" >&2 ;;
  "read status" | "migrate "* | "scope "* | "ingest "*) ;;
  "export "*)
    echo '{"record_kind":"event","event_id":"e1","key":{"scope":"adversarial-1","external_id":"R01"},"actor":{"id":"a"}}' ;;
  *)
    echo "stand-in: unexpected $*" >&2
    exit 1 ;;
esac
STANDIN
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH"

export CORE_MEMORY_BIN="$tmp/bin/core-memory-standin"
export CORE_MEMORY_DATABASE_URL=postgresql://stand-in.invalid/none
export CORE_MEMORY_ANTHROPIC_KEY=stand-in
export MAX_MINUTES=0 PAUSE_SECONDS=0 QUIET_RUNS=1
unset MODELS_CA_FILE READER_ONLY READER_EFFORT JUDGE_MODEL JUDGE_EFFORT REPEATS

cert="the model server's TLS certificate was not trusted, so nothing was sent: set MODELS_CA_FILE to the certificate authority of the proxy or gateway that re-signs it"
key="the API turned the key or a model down; see runs.log"
clean="everything was read and judged"
set3="the queue is empty, but 3 items were set aside as failed; see report.md"
set1="the queue is empty, but 1 item was set aside as failed; see report.md"

# Totals in the engine's own lane names; lanes not named are 0.
totals() { # ready failed lane
  local lane=$3 l parts=()
  for l in chunk_read tool_read judge second_read; do
    if [ "$l" = "$lane" ]; then
      parts+=("\"$l\":{\"ready\":$1,\"leased\":0,\"failed\":$2}")
    else
      parts+=("\"$l\":{\"ready\":0,\"leased\":0,\"failed\":0}")
    fi
  done
  local IFS=,
  printf '{"read":{"calls":1},"queued":{%s}}' "${parts[*]}"
}

failed=0
# case, round, ready, failed, lane, expected why
run_case() {
  local name=$1 round=$2 ready=$3 nfail=$4 lane=$5 why=$6 runner dir got
  for runner in adversarial locomo-smoke; do
    dir="$tmp/$name-$runner"
    mkdir -p "$dir"
    if [ "$runner" = locomo-smoke ]; then
      echo '[{"sample_id":"conv-26","conversation":{"session_1_date_time":"1:56 pm on 8 May, 2023","session_1":[{"dia_id":"D1:1","speaker":"A","text":"hello"}]}}]' >"$dir/locomo10.json"
    fi
    export STANDIN_ROUND=$round
    STANDIN_TOTALS=$(totals "$ready" "$nfail" "$lane")
    export STANDIN_TOTALS
    if ! timeout 60 bash "$here/$runner/run.sh" "$dir" >"$dir/stdout" 2>"$dir/stderr"; then
      echo "FAIL: $name $runner: the runner did not exit 0; see $dir/stderr" >&2
      cat "$dir/stderr" >&2
      failed=1
      continue
    fi
    got=$(grep -F "Stopped because" "$dir/report.md" | head -n1 || true)
    if [ "$got" != "Stopped because $why." ]; then
      echo "FAIL: $name $runner: expected \"Stopped because $why.\", got \"${got:-(none)}\""
      failed=1
    elif [ "$runner" = adversarial ] && [ ! -s "$dir/score.md" ]; then
      echo "FAIL: $name $runner: score.md was not written"
      failed=1
    else
      echo "ok: $name $runner"
    fi
  done
}

rej='{"read":{"calls":1,"problem":"binding_rejected"}}'
run_case rejected-spent "$rej" 0 1 chunk_read "$key"
run_case rejected-ready "$rej" 1 0 chunk_read "$key"
run_case untrusted-spent '{"read":{"calls":1,"problem":"endpoint_untrusted"}}' 0 1 chunk_read "$cert"
run_case failed-no-code '{"read":{"calls":1}}' 0 3 chunk_read "$set3"
run_case failed-other-code '{"read":{"calls":1,"problem":"model_refused"}}' 0 3 chunk_read "$set3"
run_case failed-one '{"read":{"calls":1}}' 0 1 chunk_read "$set1"
run_case failed-judge '{"judged":{"calls":1}}' 0 3 judge "$set3"
run_case clean '{"read":{"calls":1}}' 0 0 chunk_read "$clean"

exit "$failed"
