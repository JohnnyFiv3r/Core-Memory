# Sourced by eval/*/run.sh, which have set -euo pipefail on. Keeps a run's
# outputs where a later role can find them; see RESULTS.md.

here_results=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Sets root to the results folder and creates it. A global, not an echo: an
# exit inside $(...) would leave only the subshell.
results_root() {
  if [ -n "${CORE_MEMORY_RESULTS:-}" ]; then
    root=$CORE_MEMORY_RESULTS
  elif [ -d /factory/work ] && [ -w /factory/work ]; then
    root=/factory/work/core-memory-results
  else
    root="$(cd "$here_results/.." && pwd)/results"
  fi
  # The umask stays in the subshell so a run given its own folder writes as it
  # always has.
  if ! (umask 077; mkdir -p "$root/runs" "$root/findings"); then
    echo "run.sh: cannot write to $root; set CORE_MEMORY_RESULTS to a folder you can write, or give an output folder" >&2
    exit 2
  fi
  # Kept as the repository has it, so a folder made by an older version gets
  # the current instructions.
  if ! cmp -s "$here_results/RESULTS.md" "$root/README.md"; then
    (umask 077; cp "$here_results/RESULTS.md" "$root/README.md")
  fi
}

# Whether a string holds the value of a credential. Empty values are skipped:
# every string holds an empty one.
results_holds_credential() {
  local v
  for v in "${CORE_MEMORY_DATABASE_URL:-}" "${CORE_MEMORY_ANTHROPIC_KEY:-}" "${OPENAI_API_KEY:-}" "${CORE_MEMORY_OPENAI_KEY:-}"; do
    if [ -n "$v" ] && [[ $1 == *"$v"* ]]; then
      return 0
    fi
  done
  return 1
}

# Refuses, before any folder is made, a setting that would put a credential or
# a URL into the kept run: the pause and quiet limit reach report.md as typed,
# so withholding them from meta.txt alone is not enough. Names the setting,
# never its value.
results_refuse_unsafe_settings() {
  local name
  for name in REPEATS READER_ONLY READER_EFFORT JUDGE_MODEL JUDGE_EFFORT MAX_MINUTES LOCOMO_SAMPLE MODELS_CA_FILE PAUSE_SECONDS QUIET_RUNS; do
    if results_holds_credential "${!name:-}"; then
      echo "run.sh: $name holds a credential, and a run kept in the results folder would save it; unset it, or give an output folder" >&2
      exit 2
    fi
  done
  if [[ ${MODELS_CA_FILE:-} == *://* ]]; then
    echo "run.sh: MODELS_CA_FILE must be the path of a certificate file, not a URL, which a kept run would save; fix it, or give an output folder" >&2
    exit 2
  fi
}

# results_new_run <eval> <corpus>: makes a run folder under root, sets out.
results_new_run() {
  local stamp base n=1 settings name
  # Only here, not in results_root: this process is the runner, so the umask
  # reaches every file the run writes, and a run given its own folder keeps
  # the umask it always had.
  umask 077
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  base="$root/runs/$stamp-$1"
  out=$base
  # mkdir without -p is atomic, so two runs in one second cannot share a folder.
  until (umask 077; mkdir "$out" 2>/dev/null); do
    n=$((n + 1))
    if [ "$n" -gt 9 ]; then
      echo "run.sh: cannot make a run folder under $root/runs" >&2
      exit 2
    fi
    out="$base-$n"
  done
  # The config holds the endpoint and every setting as typed: it is used during
  # the run and never kept (meta.txt names what rebuilds it).
  if ! confdir=$(umask 077; mktemp -d "${TMPDIR:-/tmp}/core-memory-run.XXXXXX"); then
    echo "run.sh: cannot make a private folder for the run's config" >&2
    exit 2
  fi
  # Only these non-secret settings are named, so two runs can be told apart.
  settings=""
  for name in REPEATS READER_ONLY READER_EFFORT JUDGE_MODEL JUDGE_EFFORT MAX_MINUTES LOCOMO_SAMPLE; do
    # A setting is written only if it is plain (letters, digits, . _ - and
    # spaces) and not a credential: a run with a bad setting, such as a URL in
    # LOCOMO_SAMPLE, still fails later, but its value must not be kept first.
    if [[ ${!name:-} == *[!A-Za-z0-9._\ -]* ]] || results_holds_credential "${!name:-}"; then
      settings="$settings $name=(withheld)"
    else
      settings="$settings $name=${!name:-}"
    fi
  done
  (
    umask 077
    {
      echo "eval: $1"
      echo "started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "commit: $(git -C "$here_results" rev-parse --short HEAD 2>/dev/null || echo unknown)"
      echo "corpus: $2"
      echo "settings:$settings"
    } >"$out/meta.txt"
  )
  echo "run.sh: results go to $out" >&2
}

# Deletes the run and stops with exit 3 when a credential's value appears in
# it, or a URL appears in a file that is not corpus text. It matches whole
# values only, so it is a backstop to the engine never writing them, not a
# proof. Only ever called with the folder results_new_run made.
results_check_clean() {
  local vals=() v found=""
  [[ $1 == "$root/runs/"* ]] || { echo "run.sh: refusing to delete $1" >&2; exit 3; }
  # An empty value would be an empty pattern, which matches every line.
  for v in "${CORE_MEMORY_DATABASE_URL:-}" "${CORE_MEMORY_ANTHROPIC_KEY:-}" "${OPENAI_API_KEY:-}" "${CORE_MEMORY_OPENAI_KEY:-}"; do
    [ -n "$v" ] && vals+=("$v")
  done
  # The values go in through a process substitution, never a command line.
  if [ "${#vals[@]}" -gt 0 ] && grep -rlqF -f <(printf '%s\n' "${vals[@]}") -- "$1"; then
    found="a credential"
  # Corpus text may hold URLs (RESULTS.md allows it); nothing else a run
  # writes should.
  elif grep -rlq --exclude='events*.jsonl' --exclude=export.jsonl \
      --exclude=locomo10.json --exclude=report.md --exclude=score.md \
      -e '://' -- "$1"; then
    found="a URL"
  fi
  if [ -n "$found" ]; then
    rm -rf -- "$1"
    echo "run.sh: $found appeared in the run's results, so the run folder was deleted" >&2
    exit 3
  fi
}

# The runner's EXIT trap for a default run: drops the private config, scans
# whatever the run wrote, even when it stopped early, then says where it is.
results_finish() {
  [ -n "${confdir:-}" ] && rm -rf -- "$confdir"
  results_check_clean "$1"
  echo "run.sh: results kept in $1" >&2
}
