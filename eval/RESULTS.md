# Practical test results

Where eval runs and graded findings are kept so a later role can compare them.
This folder is outside every checkout and is never committed. It is not backed
up. It is on the same disk as /factory/state, where floor keeps its own state,
so it lasts as long as that does and is lost if the disk is replaced. It was
not checked across a container restart.

Default: /factory/work/core-memory-results. Set CORE_MEMORY_RESULTS to move it.
If /factory/work is not writable the runners use <repo>/results, which the
repository ignores.

The folder and its files are readable only by the user that made them.

## Layout

```
runs/<UTC time>-<eval>/      one folder per run: meta.txt, score.md, report.md ...
findings/<date>-<slug>.md    one graded finding per file
```

A run that stopped early keeps what it wrote. If it got as far as reporting,
`report.md` says why it stopped; a run refused at the start (a used database,
say) has no `report.md`, and says why on stderr. To delete a run, `rm -r` its
folder. A run appears here only when it ends; a folder still being copied may
be incomplete for a moment.

## Compare two runs

From the repository root, so that `eval/compare/compare.py` is found:

```bash
runs=${CORE_MEMORY_RESULTS:-/factory/work/core-memory-results}/runs
for d in "$runs"/*-adversarial*; do [ -s "$d/export.jsonl" ] && echo "$d"; done | tail -2
python3 eval/compare/compare.py before=<older run> after=<newer run>
```

The loop lists only runs that kept an export, so a run refused at the start is
skipped. Give the two paths it printed. With fewer than two, there is nothing
to compare yet: run it again.

## What may be stored

Counts, costs, pair IDs, verdict labels, scores, item numbers, graded
findings, and text from the synthetic or public eval corpora (the hard cases,
LoCoMo).

## What may not be stored

Chunk text, events.jsonl, export.jsonl or bead text from any real scope, and
any URL, key or token. A runner that can read a real scope must write only the
kinds listed above. The two runners here read only the hard cases and LoCoMo,
so they keep their text files. Each refuses a database that already holds a
scope, before any model call, and exports only its own scope (`adversarial-N`,
`locomo`). A setting that would carry a credential, or a URL in
`MODELS_CA_FILE`, is refused (exit 2) before any folder is made. A run's
`core-memory.toml` is not kept: `meta.txt` names the commit and settings that
rebuild it. A run is written to a private temporary folder and checked when
it ends. It is kept here, and its reports printed, only if no credential's
value appears anywhere in it and no URL appears outside the corpus text.
Otherwise it is deleted before anything is kept or printed, and the run exits
3. Copying a file out of this folder (into an attachment, a message or a pull
request) is publishing it: check it first.
