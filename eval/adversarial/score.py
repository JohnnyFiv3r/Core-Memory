"""Scores what the engine stored against the answers in cases.toml, for a
person to read.

    score.py <cases.toml> <export.jsonl>

The reader is scored on each message's type. The judge is scored on the
verdict of its first read of each pair the cases name. A pair with no
verdict, because the judge was never shown it or every reply about it was
rejected, is counted apart, never as a no.

Each scope in the export is one run of the cases (run.sh puts each repeat
in a scope and workspace of its own). With one run the report goes case by
case, with everything else the judge related within a thread listed,
unscored, to be read. With several it says, for each pair and each case,
how many runs got it right and what each run said: one run is a sample of
one, and the judge answers a hard pair differently from run to run.
"""

import collections
import json
import re
import sys
import tomllib

TEXT = 160
UNDIRECTED = {"none", "unsure", "near", "contains"}


def lines(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def short(text):
    text = " ".join(text.split())
    return text if len(text) <= TEXT else text[:TEXT] + "…"


def by_scope(records):
    """The export's records, run by run: each record goes with the scope of
    the event it comes from."""
    records = list(records)
    event_scope = {r["event_id"]: r["key"]["scope"] for r in records if r["record_kind"] == "event"}
    chunk_scope = {
        r["chunk_id"]: event_scope.get(r["event_id"]) for r in records if r["record_kind"] == "chunk"
    }
    # A bead a judge made has no scope of its own: it has its members'.
    made_scope = {}
    for r in records:
        if r["record_kind"] == "edge" and r["to_bead"] in chunk_scope:
            made_scope.setdefault(r["from_bead"], chunk_scope[r["to_bead"]])
    for r in records:
        if r["record_kind"] == "bead" and r.get("source") in chunk_scope:
            made_scope[r["bead_id"]] = chunk_scope[r["source"]]

    def scope(r):
        kind = r["record_kind"]
        if kind == "event":
            return r["key"]["scope"]
        if kind in ("chunk", "chunk_read"):
            return chunk_scope.get(r["chunk_id"]) if kind == "chunk_read" else event_scope.get(r["event_id"])
        if kind == "verdict":
            return r.get("later_scope") or chunk_scope.get(r["later_bead"])
        if kind == "name_link":
            return r.get("a_scope") or chunk_scope.get(r["a_bead"])
        if kind == "edge":
            return chunk_scope.get(r["to_bead"]) or chunk_scope.get(r["from_bead"]) or made_scope.get(r["from_bead"])
        if kind == "bead":
            return made_scope.get(r["bead_id"])
        return None

    runs = collections.defaultdict(list)
    for r in records:
        s = scope(r)
        if s is not None:
            runs[s].append(r)
    # adversarial-2 before adversarial-10.
    key = lambda s: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s)]
    return {s: runs[s] for s in sorted(runs, key=key)}


class Stored:
    """What one run stored, by message ID."""

    def __init__(self, records):
        kinds = collections.defaultdict(list)
        for record in records:
            kinds[record["record_kind"]].append(record)
        self.kinds = kinds
        events = {e["event_id"]: e["key"]["external_id"] for e in kinds["event"]}
        self.chunks = {c["chunk_id"]: c for c in kinds["chunk"]}
        by_message = collections.defaultdict(list)
        for chunk in sorted(kinds["chunk"], key=lambda c: c["ordinal"]):
            by_message[events.get(chunk["event_id"])].append(chunk["chunk_id"])
        self.by_message = dict(by_message)
        self.message_of = {c: m for m, cs in by_message.items() for c in cs}
        self.reads = {r["chunk_id"]: r for r in kinds["chunk_read"]}
        self.verdicts = collections.defaultdict(list)
        for v in kinds["verdict"]:
            self.verdicts[frozenset((v["earlier_bead"], v["later_bead"]))].append(v)
        self.labels = [b for b in kinds["bead"] if b.get("bead_kind") == "label"]
        self.groups = [b for b in kinds["bead"] if b.get("bead_kind") == "group"]

    def bead(self, message):
        """A message's bead: its first chunk. None if it was not stored."""
        chunks = self.by_message.get(message)
        return chunks[0] if chunks else None

    def read(self, message):
        """What the reader said of a message: a type, `abstain:<reason>`, or
        `not read`."""
        said = []
        for chunk in self.by_message.get(message, []):
            r = self.reads.get(chunk)
            if r is None:
                said.append("not read")
            elif r.get("status") == "typed":
                said.append(r["type"]["key"])
            elif r.get("status") == "abstained":
                said.append(f"abstain:{r.get('reason', '?')}")
            else:
                said.append(str(r.get("status")))
        return said or ["not stored"]

    def name(self, bead, start, end):
        chunk = self.chunks.get(bead)
        if chunk is None:
            return "?"
        return chunk["text"].encode("utf-8")[start:end].decode("utf-8", "replace")


def token(v, a_bead):
    """A verdict, written from message a's side."""
    kind = v["verdict"]
    if kind in UNDIRECTED:
        return kind
    if kind == "supersedes" and v.get("label"):
        kind = f"supersedes:{v['label']}"
    source = {
        "earlier_to_later": v["earlier_bead"],
        "later_to_earlier": v["later_bead"],
    }.get(v.get("direction"))
    return kind if source == a_bead else f"{kind} (b to a)"


def kind_of(tok):
    return tok.split(" ")[0].split(":")[0]


def read_right(case, said):
    got = said[0]
    if got in ("not read", "not stored"):
        return None
    if "not" in case:
        return all(s.split(":")[0] not in case["not"] for s in said)
    return all(s in case["expect"] or (s.startswith("abstain") and "abstain" in case["expect"]) for s in said)


def pair_right(pair, tok):
    if "not" in pair:
        return kind_of(tok) not in pair["not"]
    return tok in pair["expect"]


def answer(case):
    if "not" in case:
        return "anything but " + ", ".join(case["not"])
    return ", ".join(case["expect"])


def typed_cases(cases):
    typed = list(cases.get("read", []))
    for thread in cases.get("thread", []):
        typed.extend(
            {**m, "probe": f"in thread {thread['id']}"} for m in thread["message"] if "expect" in m or "not" in m
        )
    return typed


class Scored:
    """One run, scored."""

    def __init__(self, cases, stored):
        self.stored = stored
        self.reads = []  # (case, said, right)
        for case in typed_cases(cases):
            said = stored.read(case["id"])
            self.reads.append((case, said, read_right(case, said)))
        self.pairs = []  # (thread, pair, first, second, right, label)
        for thread in cases.get("thread", []):
            for pair in thread.get("pair", []):
                a, b = stored.bead(pair["a"]), stored.bead(pair["b"])
                found = stored.verdicts.get(frozenset((a, b)), []) if a and b else []
                first = [token(v, a) for v in found if v["read_no"] == 1]
                second = [token(v, a) for v in found if v["read_no"] == 2]
                tok = first[0] if first else None
                right = pair_right(pair, tok) if tok else None
                label = next((l["label"] for l in stored.labels if l.get("source") == a), None)
                self.pairs.append((thread, pair, tok, second, right, label))

    def totals(self):
        reads = collections.Counter({True: "right", False: "wrong", None: "not read"}[r] for *_, r in self.reads)
        pairs = collections.Counter({True: "right", False: "wrong", None: "no verdict"}[p[4]] for p in self.pairs)
        return reads, pairs


def one_run(cases, run):
    stored = run.stored
    reads, pairs = run.totals()
    w = ["# Adversarial cases\n"]
    w.append(
        f"- reader: **{reads['right']} right, {reads['wrong']} wrong** of {len(run.reads)}"
        + (f", {reads['not read']} not read" if reads["not read"] else "")
    )
    w.append(
        f"- judge: **{pairs['right']} right, {pairs['wrong']} wrong** of {len(run.pairs)} pairs"
        + (
            f", {pairs['no verdict']} with no verdict (never shown, or every reply rejected)"
            if pairs["no verdict"]
            else ""
        )
    )
    w.append("\nThe expected answers are ours (cases.toml), not a benchmark's.\n")

    by_probe = collections.defaultdict(collections.Counter)
    for case, _, right in run.reads:
        by_probe[case["probe"]][{True: "right", False: "wrong", None: "not read"}[right]] += 1
    w.append("## The reader, by probe\n")
    w.append("| Probe | Right | Wrong | Not read |")
    w.append("|---|---|---|---|")
    for probe, counted in by_probe.items():
        w.append(f"| {probe} | {counted['right']} | {counted['wrong']} | {counted['not read']} |")
    w.append("")
    wrong_reads = [(case, said) for case, said, right in run.reads if right is False]
    if wrong_reads:
        w.append("### What the reader got wrong\n")
        for case, said in wrong_reads:
            w.append(f"- **{case['id']}** ({case['probe']}): “{short(case['text'])}”")
            w.append(f"  - said: {', '.join(said)}; right: {answer(case)}")
            if case.get("why"):
                w.append(f"  - {case['why']}")
        w.append("")

    w.append("## The judge, by thread\n")
    named = {
        frozenset((stored.bead(p["a"]), stored.bead(p["b"]))) for _, p, *_ in run.pairs
    }
    for thread in cases.get("thread", []):
        w.append(f"### {thread['id']}: {thread['probe']}\n")
        for m in thread["message"]:
            w.append(f"- {m['id']} {m['speaker']}: “{short(m['text'])}” ({', '.join(stored.read(m['id']))})")
        w.append("")
        for t, pair, tok, second, right, _ in run.pairs:
            if t is not thread:
                continue
            mark = {True: "right", False: "**wrong**", None: "no verdict"}[right]
            again = f"; second read: {', '.join(second)}" if second else ""
            w.append(f"- {pair['a']} → {pair['b']}: {mark}. Said {tok or '-'}{again}; right: {answer(pair)}")
            if right is False and pair.get("why"):
                w.append(f"  - {pair['why']}")
        # Everything else the judge related among this thread's messages.
        beads = {stored.bead(m["id"]): m["id"] for m in thread["message"]}
        beads.pop(None, None)
        other = []
        for key, found in stored.verdicts.items():
            if key in named or not key <= set(beads):
                continue
            for v in found:
                if v["read_no"] == 1 and v["verdict"] != "none":
                    a, b = v["later_bead"], v["earlier_bead"]
                    other.append(f"{beads[a]} → {beads[b]}: {token(v, a)}")
        if other:
            w.append("- also said, unscored: " + "; ".join(sorted(other)))
        for same in thread.get("names", []):
            want = set(same["same"])
            hit = any(
                {stored.name(n["a_bead"], n["a_start"], n["a_end"]), stored.name(n["b_bead"], n["b_start"], n["b_end"])}
                == want
                for n in stored.kinds["name_link"]
                if {n["a_bead"], n["b_bead"]} <= set(beads)
            )
            w.append(f"- names {' = '.join(same['same'])}: {'linked' if hit else '**not linked**'}")
        for banned in thread.get("no_name", []):
            bad = [
                n
                for n in stored.kinds["name_link"]
                if banned["name"]
                in (stored.name(n["a_bead"], n["a_start"], n["a_end"]), stored.name(n["b_bead"], n["b_start"], n["b_end"]))
            ]
            w.append(f"- “{banned['name']}” linked to nothing: {'**no, linked**' if bad else 'right'}")
        made = [b for b in stored.labels if b.get("source") in beads]
        if made:
            w.append("- label beads: " + ", ".join(f"{beads[b['source']]} {b['label']}" for b in made))
        w.append("")

    names = [
        f"“{stored.name(n['a_bead'], n['a_start'], n['a_end'])}” ({stored.message_of.get(n['a_bead'], '?')}) = "
        f"“{stored.name(n['b_bead'], n['b_start'], n['b_end'])}” ({stored.message_of.get(n['b_bead'], '?')})"
        for n in stored.kinds["name_link"]
    ]
    w.append("## Every name link\n")
    if names:
        w.extend(f"- {n}" for n in names)
    else:
        w.append("None.")
    return w


def several_runs(cases, runs):
    n = len(runs)
    w = [f"# Adversarial cases, {n} runs\n"]
    w.append("| Run | Reader right | Judge right | Wrong | No verdict |")
    w.append("|---|---|---|---|---|")
    for name, run in runs.items():
        reads, pairs = run.totals()
        w.append(
            f"| {name} | {reads['right']} of {len(run.reads)} | {pairs['right']} of {len(run.pairs)} "
            f"| {pairs['wrong']} | {pairs['no verdict']} |"
        )
    w.append("\nThe expected answers are ours (cases.toml), not a benchmark's.\n")

    w.append("## The judge, pair by pair\n")
    w.append("Each run's verdict of its first read; the label it made, if any, after a `→`.\n")
    first = next(iter(runs.values()))
    for i, (thread, pair, *_) in enumerate(first.pairs):
        said, right = [], 0
        for run in runs.values():
            _, _, tok, _, ok, label = run.pairs[i]
            right += ok is True
            said.append((tok or "no verdict") + (f" → {label}" if label else ""))
        mark = f"**{right} of {n}**" if right < n else f"{right} of {n}"
        w.append(f"- {thread['id']} {pair['a']} → {pair['b']} ({thread['probe']}): {mark}. {'; '.join(said)}. Right: {answer(pair)}")
    w.append("")

    groups = {name: len(run.stored.groups) for name, run in runs.items()}
    w.append(f"Group beads (memories the judge joined): {', '.join(f'{k} {v}' for k, v in groups.items())}.\n")

    w.append("## The reader: cases not right in every run\n")
    cases_seen = [case for case, *_ in first.reads]
    shown = 0
    for i, case in enumerate(cases_seen):
        answers = [run.reads[i] for run in runs.values()]
        right = sum(1 for *_, ok in answers if ok is True)
        if right == n:
            continue
        shown += 1
        said = "; ".join(", ".join(s) for _, s, _ in answers)
        w.append(f"- **{case['id']}** ({case['probe']}) {right} of {n}: “{short(case['text'])}” Said: {said}. Right: {answer(case)}")
    if not shown:
        w.append("None.")
    return w


def main(cases_path, export):
    with open(cases_path, "rb") as f:
        cases = tomllib.load(f)
    runs = {name: Scored(cases, Stored(records)) for name, records in by_scope(lines(export)).items()}
    if not runs:
        sys.exit("score.py: the export holds no events")
    w = one_run(cases, next(iter(runs.values()))) if len(runs) == 1 else several_runs(cases, runs)
    print("\n".join(w))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
