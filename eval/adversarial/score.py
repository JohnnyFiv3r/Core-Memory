"""Scores what the engine stored against the answers in cases.toml, for a
person to read.

    score.py <cases.toml> <export.jsonl>

The reader is scored on each message's type. The judge is scored on the
verdict of its first read of each pair the cases name; a pair it was never
shown is counted apart, never as a no. Everything else the judge related
within a thread is listed too, unscored, to be read.
"""

import collections
import json
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


class Stored:
    """What the export says, by message ID."""

    def __init__(self, export):
        kinds = collections.defaultdict(list)
        for record in lines(export):
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


def main(cases_path, export):
    with open(cases_path, "rb") as f:
        cases = tomllib.load(f)
    stored = Stored(export)
    text = {c["id"]: c["text"] for c in cases.get("read", [])}
    for thread in cases.get("thread", []):
        for m in thread["message"]:
            text[m["id"]] = m["text"]

    # ---- the reader ----
    typed = list(cases.get("read", []))
    for thread in cases.get("thread", []):
        typed.extend(
            {**m, "probe": f"in thread {thread['id']}"} for m in thread["message"] if "expect" in m or "not" in m
        )
    by_probe = collections.defaultdict(collections.Counter)
    wrong_reads = []
    for case in typed:
        said = stored.read(case["id"])
        right = read_right(case, said)
        outcome = {True: "right", False: "wrong", None: "not read"}[right]
        by_probe[case["probe"]][outcome] += 1
        if right is False:
            wrong_reads.append((case, said))

    # ---- the judge ----
    pair_results = []
    shown_pairs = set()
    for thread in cases.get("thread", []):
        for pair in thread.get("pair", []):
            a, b = stored.bead(pair["a"]), stored.bead(pair["b"])
            found = stored.verdicts.get(frozenset((a, b)), []) if a and b else []
            shown_pairs.add(frozenset((a, b)))
            first = [v for v in found if v["read_no"] == 1]
            second = [v for v in found if v["read_no"] == 2]
            tok = token(first[0], a) if first else None
            right = pair_right(pair, tok) if tok else None
            pair_results.append((thread, pair, tok, [token(v, a) for v in second], right))

    w = ["# Adversarial cases\n"]
    reads = collections.Counter()
    for counted in by_probe.values():
        reads.update(counted)
    pairs = collections.Counter({True: "right", False: "wrong", None: "not judged"}[r] for *_, r in pair_results)
    w.append(
        f"- reader: **{reads['right']} right, {reads['wrong']} wrong** of {sum(reads.values())}"
        + (f", {reads['not read']} not read" if reads["not read"] else "")
    )
    w.append(
        f"- judge: **{pairs['right']} right, {pairs['wrong']} wrong** of {len(pair_results)} pairs"
        + (f", {pairs['not judged']} never shown to it" if pairs["not judged"] else "")
    )
    w.append("\nThe expected answers are ours (cases.toml), not a benchmark's.\n")

    w.append("## The reader, by probe\n")
    w.append("| Probe | Right | Wrong | Not read |")
    w.append("|---|---|---|---|")
    for probe, counted in by_probe.items():
        w.append(f"| {probe} | {counted['right']} | {counted['wrong']} | {counted['not read']} |")
    w.append("")
    if wrong_reads:
        w.append("### What the reader got wrong\n")
        for case, said in wrong_reads:
            w.append(f"- **{case['id']}** ({case['probe']}): “{short(case['text'])}”")
            w.append(f"  - said: {', '.join(said)}; right: {answer(case)}")
            if case.get("why"):
                w.append(f"  - {case['why']}")
        w.append("")

    w.append("## The judge, by thread\n")
    for thread in cases.get("thread", []):
        w.append(f"### {thread['id']}: {thread['probe']}\n")
        for m in thread["message"]:
            w.append(f"- {m['id']} {m['speaker']}: “{short(m['text'])}” ({', '.join(stored.read(m['id']))})")
        w.append("")
        for t, pair, tok, second, right in pair_results:
            if t is not thread:
                continue
            mark = {True: "right", False: "**wrong**", None: "never shown"}[right]
            again = f"; second read: {', '.join(second)}" if second else ""
            w.append(f"- {pair['a']} → {pair['b']}: {mark}. Said {tok or '-'}{again}; right: {answer(pair)}")
            if right is False and pair.get("why"):
                w.append(f"  - {pair['why']}")
        # Everything else the judge related among this thread's messages.
        beads = {stored.bead(m["id"]): m["id"] for m in thread["message"]}
        beads.pop(None, None)
        other = []
        for key, found in stored.verdicts.items():
            if key in shown_pairs or not key <= set(beads):
                continue
            for v in found:
                if v["read_no"] == 1 and v["verdict"] != "none":
                    a, b = v["later_bead"], v["earlier_bead"]
                    other.append(f"{beads[a]} → {beads[b]}: {token(v, a)}")
        if other:
            w.append("- also said, unscored: " + "; ".join(sorted(other)))
        # Names.
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
    print("\n".join(w))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
