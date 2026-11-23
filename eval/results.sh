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
  # mkdir and cp leave an existing folder or file as it was, so one made with a
  # looser mode would stay readable by others. The folders are fixed first: a
  # read-only README can only be replaced by a process that may write its folder.
  if ! chmod 700 "$root" "$root/runs" "$root/findings"; then
    echo "run.sh: cannot make $root private to you; set CORE_MEMORY_RESULTS to a folder you own, or give an output folder" >&2
    exit 2
  fi
  # Kept as the repository has it, so a folder made by an older version gets
  # the current instructions.
  if ! cmp -s "$here_results/RESULTS.md" "$root/README.md"; then
    # Written beside it and renamed over it, so a copy cut short leaves the
    # previous complete README, and a read-only one is replaced all the same.
    readme_new="$root/.README.md.$$"
    if ! (umask 077; cp "$here_results/RESULTS.md" "$readme_new" && mv -f -- "$readme_new" "$root/README.md"); then
      rm -f -- "$readme_new"
      echo "run.sh: cannot write $root/README.md; set CORE_MEMORY_RESULTS to a folder you own, or give an output folder" >&2
      exit 2
    fi
  fi
  if ! chmod 600 "$root/README.md"; then
    echo "run.sh: cannot make $root/README.md private to you; set CORE_MEMORY_RESULTS to a folder you own, or give an output folder" >&2
    exit 2
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

# results_new_run <eval> <corpus>: makes a private staging folder and sets out
# (the run's files), confdir (its config), stage (both) and run_base (where
# results_publish keeps a clean run). Nothing reaches root until then.
results_new_run() {
  local stamp settings name
  # Only here, not in results_root: this process is the runner, so the umask
  # reaches every file the run writes, and a run given its own folder keeps
  # the umask it always had.
  umask 077
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  run_base="$root/runs/$stamp-$1"
  # A run is written here and kept only once it has been checked, so a value
  # that should not be kept never reaches the shared folder or the terminal.
  if ! stage=$(umask 077; mktemp -d "${TMPDIR:-/tmp}/core-memory-run.XXXXXX"); then
    echo "run.sh: cannot make a private folder for the run" >&2
    exit 2
  fi
  out="$stage/run"
  # The config holds the endpoint and every setting as typed: it is used during
  # the run and never kept (meta.txt names what rebuilds it).
  confdir="$stage/config"
  (umask 077; mkdir "$out" "$confdir")
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
  echo "run.sh: the run is kept under $root/runs once it has been checked" >&2
}

# Deletes the staged run and stops with exit 3 when a credential's value
# appears in it, or a URL appears in a file that is not corpus text. It
# matches whole values only, so it is a backstop to the engine never writing
# them, not a proof. Only ever called with the staged run results_new_run made.
results_check_clean() {
  local vals=() v found="" rc
  [[ $1 == "$stage/run" ]] || { echo "run.sh: refusing to delete $1" >&2; exit 3; }
  # An empty value would be an empty pattern, which matches every line.
  for v in "${CORE_MEMORY_DATABASE_URL:-}" "${CORE_MEMORY_ANTHROPIC_KEY:-}" "${OPENAI_API_KEY:-}" "${CORE_MEMORY_OPENAI_KEY:-}"; do
    [ -n "$v" ] && vals+=("$v")
  done
  # grep says 0 for a match, 1 for a verified miss and 2 or more when it could
  # not read everything, so only 1 means the run was checked and is clean.
  # The values go in through a process substitution, never a command line.
  if [ "${#vals[@]}" -gt 0 ]; then
    rc=0
    grep -rlqF -f <(printf '%s\n' "${vals[@]}") -- "$1" || rc=$?
    case $rc in
      0) found="a credential" ;;
      1) ;;
      *) found="a scan error" ;;
    esac
  fi
  # Corpus text may hold URLs (RESULTS.md allows it); nothing else a run
  # writes should.
  if [ -z "$found" ]; then
    rc=0
    grep -rlq --exclude='events*.jsonl' --exclude=export.jsonl \
      --exclude=locomo10.json --exclude=report.md --exclude=score.md \
      -e '://' -- "$1" || rc=$?
    case $rc in
      0) found="a URL" ;;
      1) ;;
      *) found="a scan error" ;;
    esac
  fi
  if [ -n "$found" ]; then
    rm -rf -- "$stage"
    # So the EXIT trap has nothing left to keep.
    stage=""
    if [ "$found" = "a scan error" ]; then
      echo "run.sh: the run's results could not be checked for credentials, so the run was deleted before it was kept" >&2
    else
      echo "run.sh: $found appeared in the run's results, so the run was deleted before it was kept" >&2
    fi
    exit 3
  fi
}

# Checks the staged run, and only then copies it to runs/ and sets out to the
# kept folder. A copy, not a move: TMPDIR and the results folder may be on
# different filesystems. For a moment the kept folder is partly copied.
results_publish() {
  local n=1 final=$run_base
  results_check_clean "$out"
  # mkdir without -p is atomic, so two runs in one second cannot share a folder.
  until (umask 077; mkdir "$final" 2>/dev/null); do
    n=$((n + 1))
    if [ "$n" -gt 9 ]; then
      echo "run.sh: cannot make a run folder under $root/runs" >&2
      exit 2
    fi
    final="$run_base-$n"
  done
  # The config is not needed past here, so whatever a failed copy leaves behind
  # holds none.
  rm -rf -- "$confdir"
  if ! cp -pR -- "$out/." "$final/"; then
    # Not a second copy under another name, which would leave this folder empty
    # beside it: remove the partial one and keep the staged run for a retry.
    rm -rf -- "$final"
    echo "run.sh: the run could not be copied to $root/runs; it was checked and is still in $out" >&2
    publish_failed=1
    exit 2
  fi
  rm -rf -- "$stage"
  stage=""
  out=$final
  echo "run.sh: results kept in $out" >&2
}

# The runner's EXIT trap for a default run: drops the private config and keeps
# what an early stop wrote, after the same check. Does nothing once the run
# has been published or deleted.
results_finish() {
  [ -n "${stage:-}" ] || return 0
  # A copy that failed once is not tried again under another name.
  [ -z "${publish_failed:-}" ] || return 0
  rm -rf -- "$confdir"
  results_publish
}
