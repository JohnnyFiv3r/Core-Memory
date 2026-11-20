"""What a smoke-test run did: what was read and judged, what it cost, and
samples of what the judge said, to be read by a person. run.sh runs it.

    report.py <output-folder> <why the run stopped>
    report.py --left <log> <rounds>   of one `read run`; prints: calls owed rejected
"""

import collections
import json
import sys
from pathlib import Path

# Dollars per million tokens, input and output, for prompts of up to 100K
# tokens: https://platform.claude.com/docs/en/about-claude/pricing
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}
# What the provider charges for input it reads from its prompt cache, as a
# part of the input price: a tenth on most models, a twentieth on Opus 5.5.
CACHE_READ = {"claude-haiku-5-5": 0.1, "claude-opus-5-5": 0.05}
# And for input it writes there for five minutes, on every model.
CACHE_WRITE = 1.25
SAMPLES = 6
TEXT = 200


def lines(path):
    """The JSON objects of a JSON Lines file, skipping anything else."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
    except FileNotFoundError:
        return


def left(log, rounds):
    """Calls one `read run` made, the work it left ready or leased, and why
    no call can succeed until someone acts: 1 if the API turned a binding
    down, 2 if the model server's certificate was not trusted, 0 otherwise.
    A run that ended without its totals owes work still."""
    calls, owed = 0, 1
    for line in lines(log):
        if "queued" in line and "read" in line:
            calls = line["read"]["calls"]
            owed = sum(w["ready"] + w["leased"] for w in line["queued"].values())
    problems = {
        (round_.get("read") or round_.get("judged") or {}).get("problem")
        for round_ in lines(rounds)
    }
    if "endpoint_untrusted" in problems:
        rejected = 2
    elif "binding_rejected" in problems:
        rejected = 1
    else:
        rejected = 0
    print(calls, owed, rejected)


def report(out, why):
    out = Path(out)
    check = list(lines(out / "check.jsonl"))
    models = {c["binding"]: c["model"] for c in check if "binding" in c and "model" in c}
    reader = next(c["reader"]["binding"] for c in check if "reader" in c)
    # A run that only reads has no judge.
    judge = next((c["judge"]["binding"] for c in check if "judge" in c), None)

    kinds = collections.defaultdict(list)
    for record in lines(out / "export.jsonl"):
        kinds[record["record_kind"]].append(record)
    events = {r["event_id"]: r for r in kinds["event"]}
    chunks = {r["chunk_id"]: r for r in kinds["chunk"]}
    beads = {r["bead_id"]: r for r in kinds["bead"]}
    # A tool call's bead is its event: it is shown by its first chunk.
    first_chunk = {}
    for chunk in sorted(chunks.values(), key=lambda c: c["ordinal"]):
        first_chunk.setdefault(chunk["event_id"], chunk["chunk_id"])

    def said(bead):
        bead = first_chunk.get(bead, bead)
        if bead in chunks:
            chunk = chunks[bead]
            event = events.get(chunk["event_id"], {})
            who = (event.get("actor") or {}).get("id", "?")
            where = (event.get("key") or {}).get("external_id", "?")
            text = " ".join(chunk["text"].split())
            more = "…" if len(text) > TEXT else ""
            return f"[{where} {who}] {text[:TEXT]}{more}"
        made = beads.get(bead)
        if made:
            return f"(a {made['bead_kind']} bead)"
        return f"(bead {bead[:8]}, not in this export)"

    def name(bead, start, end):
        chunk = chunks.get(bead)
        if chunk is None:
            return "?"
        return chunk["text"].encode("utf-8")[start:end].decode("utf-8", "replace")

    # ---- what the models cost, from what the provider reported ----
    spent = {reader: collections.Counter()}
    if judge:
        spent[judge] = collections.Counter()
    problems = collections.Counter()
    faults = collections.Counter()
    for round_ in lines(out / "rounds.jsonl"):
        for kind, binding in (("read", reader), ("judged", judge)):
            if kind in round_ and binding in spent:
                said_ = round_[kind]
                spent[binding]["rounds"] += 1
                for field in (
                    "calls",
                    "input_tokens",
                    "output_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "quotes_dropped",
                ):
                    spent[binding][field] += said_.get(field, 0)
                if said_.get("problem"):
                    problems[f"{binding}: {said_['problem']}"] += 1
                for fault in said_.get("reply_faults", []):
                    faults[f"{binding}: {fault}"] += 1

    w = []
    w.append(f"# LoCoMo smoke test\n\nStopped because {why or 'it was stopped'}.\n")
    w.append("## What was stored\n")
    w.append(f"- {len(events)} events, {len(chunks)} chunks")
    reads = {r["chunk_id"]: r for r in kinds["chunk_read"]}
    statuses = collections.Counter(r["status"] for r in reads.values())
    types = collections.Counter(
        r["type"]["key"] for r in reads.values() if isinstance(r.get("type"), dict)
    )
    how = ", ".join(f"{n} {s}" for s, n in statuses.most_common())
    w.append(f"- chunks read: {len(reads)} of {len(chunks)}" + (f" ({how})" if how else ""))
    if types:
        w.append(f"- types: {', '.join(f'{k} {n}' for k, n in types.most_common())}")
    abstained = collections.Counter(
        r["reason"] for r in reads.values() if r.get("status") == "abstained"
    )
    if abstained:
        w.append(f"- abstained: {', '.join(f'{k} {n}' for k, n in abstained.most_common())}")
    quoted = collections.Counter(
        span["kind"] for r in reads.values() for span in r.get("spans", [])
    )
    with_quotes = sum(1 for r in reads.values() if r.get("spans"))
    w.append(
        f"- quotes kept: {', '.join(f'{k} {n}' for k, n in sorted(quoted.items())) or 'none'}"
        f", in {with_quotes} of {len(reads)} reads"
    )
    verdicts = collections.Counter(
        (v["read_no"], v["verdict"]) for v in kinds["verdict"]
    )
    for read_no, title in ((1, "verdicts, first read"), (2, "verdicts, second read")):
        counted = {v: n for (r, v), n in verdicts.items() if r == read_no}
        if counted:
            ordered = sorted(counted.items(), key=lambda kv: -kv[1])
            w.append(f"- {title}: {', '.join(f'{v} {n}' for v, n in ordered)}")
    arrows = collections.Counter(
        (v["verdict"], v["arrow"]) for v in kinds["verdict"] if v["read_no"] == 1 and v.get("arrow")
    )
    if arrows:
        w.append(
            "- arrows, first read: "
            + ", ".join(f"{a} ({t}) {n}" for (t, a), n in arrows.most_common())
        )
    edges = collections.defaultdict(list)
    for edge in kinds["edge"]:
        edges[edge["edge_type"]].append(edge)
    w.append(
        "- edges: "
        + (", ".join(f"{t} {len(e)}" for t, e in sorted(edges.items())) or "none")
    )
    ended = collections.Counter(
        e["arrow"] for e in kinds["edge"] if e.get("arrow") in ("repl", "withdraws")
    )
    groups = sum(1 for b in beads.values() if b["bead_kind"] == "group")
    w.append(
        "- written, ending currency: "
        + (", ".join(f"{k} {n}" for k, n in ended.most_common()) or "none")
    )
    w.append(f"- group beads: {groups}")
    w.append(f"- name links: {len(kinds['name_link'])}\n")

    w.append("## Calls and cost\n")
    w.append(
        "| Binding | Model | Rounds | Calls | Input tokens | of them cached, read / written "
        "| Output tokens | Cost |"
    )
    w.append("|---|---|---|---|---|---|---|---|")
    total = 0.0
    for binding, counted in spent.items():
        model = models.get(binding, "?")
        price_in, price_out = PRICES.get(model, (0.0, 0.0))
        read, written = counted["cache_read_tokens"], counted["cache_write_tokens"]
        plain = counted["input_tokens"] - read - written
        cost = (
            (plain + read * CACHE_READ.get(model, 0.1) + written * CACHE_WRITE) * price_in
            + counted["output_tokens"] * price_out
        ) / 1e6
        total += cost
        w.append(
            f"| {binding} | {model} | {counted['rounds']} | {counted['calls']} | "
            f"{counted['input_tokens']:,} | {read:,} / {written:,} | "
            f"{counted['output_tokens']:,} | ${cost:.2f} |"
        )
    w.append(f"\nTotal: **${total:.2f}**, from the token counts the API reported.\n")
    if problems:
        w.append("Rounds that reported a problem:")
        w.extend(f"- {p}: {n}" for p, n in problems.most_common())
        w.append("")
    if faults:
        w.append("Rounds with a rejected reply, by what was wrong with it:")
        w.extend(f"- {f}: {n}" for f, n in faults.most_common())
        w.append("")
    dropped = spent[reader]["quotes_dropped"]
    if dropped:
        w.append(f"Quotes the reader's stored reads left out: {dropped}.\n")

    w.append("## Work left\n")
    for status in lines(out / "status.jsonl"):
        if "work" in status:
            owed = ", ".join(f"{p} {n}" for p, n in sorted(status.get("problems", {}).items()))
            w.append(
                f"- {status['work']}: {status['ready']} ready, {status['leased']} leased, "
                f"{status['failed']} failed{'; ' + owed if owed else ''}"
            )
    w.append("")

    if not judge:
        print("\n".join(w))
        return
    w.append("## What the judge said, to read\n")
    if not edges and not kinds["name_link"]:
        w.append("Nothing.\n")
    for edge_type, found in sorted(edges.items()):
        arrow = "—" if edge_type == "near" else "→"
        w.append(f"### {edge_type} ({len(found)})\n")
        for edge in found[:SAMPLES]:
            w.append(f"- {said(edge['from_bead'])}\n  {arrow} {said(edge['to_bead'])}")
        w.append("")
    if kinds["name_link"]:
        w.append(f"### Name links ({len(kinds['name_link'])})\n")
        for link in kinds["name_link"][:SAMPLES]:
            a = name(link["a_bead"], link["a_start"], link["a_end"])
            b = name(link["b_bead"], link["b_start"], link["b_end"])
            w.append(f"- “{a}” = “{b}”")
        w.append("")
    print("\n".join(w))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--left":
        left(sys.argv[2], sys.argv[3])
    elif len(sys.argv) in (2, 3):
        report(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else "")
    else:
        sys.exit(__doc__)
