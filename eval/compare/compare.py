"""Compares what different judges said about the same messages, pair by
pair, to be read by a person.

    compare.py <label>=<run folder or export.jsonl>[:<scope>] ...

Each argument is one run: a run folder of the smoke test or of the hard
cases (its export.jsonl, and check.jsonl and rounds.jsonl beside it if they
are there), or an export by itself. An export with several scopes, as the
hard cases write with REPEATS, is that many runs, `<label>/<scope>`, unless
a scope is named.

Runs are compared by message: a pair is two messages' external IDs, so runs
in different databases, workspaces or scopes line up. What is compared is
each pair's first read, written from the side of the message whose ID sorts
first, and the arrows that end currency and were written.

Two runs of one judge disagree too: a judge answers a hard pair differently
from run to run. So the report puts the agreement between runs of one model
beside the agreement between models. A gap between them is what the models
differ by; agreement as low within a model as across is noise.
"""

import collections
import itertools
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adversarial"))
sys.path.insert(0, str(HERE.parent / "locomo-smoke"))
import report as smoke  # noqa: E402
import score  # noqa: E402

TEXT = 140
# How many disagreements of each kind are listed for a person to read.
LISTED = 12


class Run:
    def __init__(self, label, records, folder):
        self.label = label
        stored = score.Stored(records)
        self.stored = stored
        self.model = "?"
        self.cost = None
        if folder is not None:
            check = list(score.lines(folder / "check.jsonl")) if (folder / "check.jsonl").exists() else []
            judge = next((c["judge"]["binding"] for c in check if "judge" in c), None)
            models = {c["binding"]: c["model"] for c in check if "binding" in c and "model" in c}
            self.model = models.get(judge, "?")
            self.cost = judge_cost(folder / "rounds.jsonl", self.model)
        # A pair's first read, from the side of the message that sorts first.
        self.first = {}
        for verdicts in stored.verdicts.values():
            for v in verdicts:
                if v.get("read_no") != 1:
                    continue
                a_msg = stored.message_of.get(v["earlier_bead"])
                b_msg = stored.message_of.get(v["later_bead"])
                if a_msg is None or b_msg is None or a_msg == b_msg:
                    continue
                a, b = sorted((a_msg, b_msg))
                self.first[(a, b)] = score.token(v, stored.bead(a))
        # The arrows that end currency and were written: (source, ended).
        self.ended = set()
        for e in stored.chains:
            src = stored.message_of.get(e["from_bead"])
            dst = stored.message_of.get(e["to_bead"])
            if src and dst:
                self.ended.add((e["arrow"], src, dst))
        self.groups = len(stored.groups)

    def text(self, message):
        bead = self.stored.bead(message)
        chunk = self.stored.chunks.get(bead)
        return score.short(chunk["text"])[:TEXT] if chunk else "?"


def judge_cost(rounds_path, model):
    """What the judge's calls cost, by the provider's token counts."""
    if not rounds_path.exists():
        return None
    counted = collections.Counter()
    for r in score.lines(rounds_path):
        said = r.get("judged")
        if said:
            for field in ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                counted[field] += said.get(field, 0)
    price_in, price_out = smoke.PRICES.get(model, (0.0, 0.0))
    read, written = counted["cache_read_tokens"], counted["cache_write_tokens"]
    plain = counted["input_tokens"] - read - written
    dollars = (
        (plain + read * smoke.CACHE_READ.get(model, 0.1) + written * smoke.CACHE_WRITE) * price_in
        + counted["output_tokens"] * price_out
    ) / 1e6
    return counted["calls"], dollars


def kind(tok):
    """A first read's kind: none, unsure, or its arrow's type."""
    if tok in ("none", "unsure"):
        return tok
    return score.TYPE_OF.get(tok, "?")


def kappa(xs, ys):
    """Cohen's kappa of two lists of labels: agreement beyond chance."""
    n = len(xs)
    if n == 0:
        return None
    observed = sum(x == y for x, y in zip(xs, ys)) / n
    cx, cy = collections.Counter(xs), collections.Counter(ys)
    expected = sum(cx[k] * cy[k] for k in cx) / (n * n)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def agreement(x, y):
    shared = sorted(set(x.first) & set(y.first))
    tx = [x.first[p] for p in shared]
    ty = [y.first[p] for p in shared]
    related = lambda t: t not in ("none", "unsure")
    both_related = [i for i in range(len(shared)) if related(tx[i]) and related(ty[i])]
    ended_x = {(s, d) for _, s, d in x.ended}
    ended_y = {(s, d) for _, s, d in y.ended}
    return {
        "shared": len(shared),
        "only_x": len(set(x.first) - set(y.first)),
        "only_y": len(set(y.first) - set(x.first)),
        "related": sum(related(a) == related(b) for a, b in zip(tx, ty)),
        "kind": sum(kind(a) == kind(b) for a, b in zip(tx, ty)),
        "kappa": kappa([kind(t) for t in tx], [kind(t) for t in ty]),
        "both_related": len(both_related),
        "same_arrow": sum(tx[i] == ty[i] for i in both_related),
        "ended_both": len(ended_x & ended_y),
        "ended_either": len(ended_x | ended_y),
        "pairs": shared,
        "tx": tx,
        "ty": ty,
    }


def pct(n, d):
    return f"{100 * n / d:.0f}%" if d else "—"


def load(arg):
    if "=" not in arg:
        sys.exit(f"compare.py: {arg!r} is not <label>=<path>\n{__doc__}")
    label, path = arg.split("=", 1)
    scope = None
    if ":" in path and not os.path.exists(path):
        path, scope = path.rsplit(":", 1)
    path = Path(path)
    folder = path if path.is_dir() else path.parent
    export = path / "export.jsonl" if path.is_dir() else path
    if not export.exists():
        sys.exit(f"compare.py: no export at {export}")
    runs = score.by_scope(score.lines(export))
    if scope is not None:
        if scope not in runs:
            sys.exit(f"compare.py: {export} has no scope {scope!r}; it has {', '.join(runs)}")
        runs = {scope: runs[scope]}
    if len(runs) == 1:
        (only,) = runs.values()
        return [Run(label, only, folder)]
    out = [Run(f"{label}/{s}", records, folder) for s, records in runs.items()]
    # The rounds are the folder's, not one scope's: its cost is shown once.
    for run in out[1:]:
        run.cost = ("″", None)
    return out


def main(args):
    if len(args) < 2 and not (len(args) == 1 and "=" in args[0]):
        sys.exit(__doc__)
    runs = [run for arg in args for run in load(arg)]
    if len(runs) < 2:
        sys.exit("compare.py: give at least two runs")

    w = ["# Judges compared\n"]
    w.append("| Run | Judge model | Pairs read | Related | Ended, written | Groups | Judge calls | Judge cost |")
    w.append("|---|---|---|---|---|---|---|---|")
    for r in runs:
        related = sum(t not in ("none", "unsure") for t in r.first.values())
        calls, dollars = r.cost if r.cost else ("?", None)
        shown = f"${dollars:.2f}" if dollars is not None else ("″" if calls == "″" else "?")
        w.append(
            f"| {r.label} | {r.model} | {len(r.first)} | {related} | {len(r.ended)} | {r.groups} "
            f"| {calls} | {shown} |"
        )
    if any(r.cost and r.cost[0] == "″" for r in runs):
        w.append("\n″: the same run folder as the row above it; its cost is the folder's, for all its scopes.")
    w.append("")

    w.append("## Agreement, run against run\n")
    w.append(
        "Over the pairs both runs read. *Related*: both said the two are related, or both said "
        "not (`none` or `unsure`). *Type*: the same arrow type, or both `none`, or both `unsure`. "
        "*κ*: type agreement beyond chance. *Arrow*: the same arrow, of the pairs both related. "
        "*Ended*: arrows that end currency written by both, of those written by either.\n"
    )
    w.append("| Runs | Models | Pairs both read | Related | Type | κ | Arrow | Ended |")
    w.append("|---|---|---|---|---|---|---|---|")
    by_models = collections.defaultdict(list)
    results = {}
    for x, y in itertools.combinations(runs, 2):
        a = agreement(x, y)
        results[(x.label, y.label)] = a
        models = " · ".join(sorted({x.model, y.model})) if x.model != y.model else f"{x.model} ×2"
        by_models[tuple(sorted((x.model, y.model)))].append(a)
        k = "—" if a["kappa"] is None else f"{a['kappa']:.2f}"
        w.append(
            f"| {x.label} · {y.label} | {models} | {a['shared']} | {pct(a['related'], a['shared'])} "
            f"| {pct(a['kind'], a['shared'])} | {k} | {pct(a['same_arrow'], a['both_related'])} "
            f"| {a['ended_both']} of {a['ended_either']} |"
        )
    w.append("")

    w.append("## Within a model and across models\n")
    w.append("The same measures, summed over every pair of runs of each kind.\n")
    w.append("| Models | Pairs of runs | Related | Type | Arrow | Ended |")
    w.append("|---|---|---|---|---|---|")
    for models, found in sorted(by_models.items()):
        name = f"{models[0]} ×2" if models[0] == models[1] else f"{models[0]} · {models[1]}"
        total = lambda f: sum(a[f] for a in found)
        w.append(
            f"| {name} | {len(found)} | {pct(total('related'), total('shared'))} "
            f"| {pct(total('kind'), total('shared'))} | {pct(total('same_arrow'), total('both_related'))} "
            f"| {total('ended_both')} of {total('ended_either')} |"
        )
    w.append("")

    # What a person should read: pairs where one model ended a statement and
    # the other did not, or where they disagree on related at all.
    models = sorted({r.model for r in runs})
    if len(models) == 2:
        first_of = {m: [r for r in runs if r.model == m] for m in models}
        w.append(f"## Where {models[0]} and {models[1]} disagree, to read\n")
        w.append("Each pair's first read by every run, from the first message's side.\n")
        pairs = sorted(set().union(*(set(r.first) for r in runs)))
        currency, related = [], []
        for p in pairs:
            said = {m: [r.first.get(p, "—") for r in first_of[m]] for m in models}
            ends = {m: any(t in score.ENDS for t in said[m]) for m in models}
            rel = {
                m: [t not in ("none", "unsure") for t in said[m] if t != "—"] for m in models
            }
            x, y = rel[models[0]], rel[models[1]]
            if ends[models[0]] != ends[models[1]]:
                currency.append((p, said))
            elif x and y and ((all(x) and not any(y)) or (all(y) and not any(x))):
                # Every run of one model related the two; no run of the
                # other did.
                related.append((p, said))
        source = runs[0]
        for title, found in (
            ("One model ended a statement and the other did not", currency),
            ("One model always related the two and the other never did", related),
        ):
            w.append(f"### {title} ({len(found)})\n")
            for (a, b), said in found[:LISTED]:
                w.append(f"- **{a}** “{source.text(a)}”")
                w.append(f"  **{b}** “{source.text(b)}”")
                w.append("  " + "; ".join(f"{m}: {', '.join(said[m])}" for m in models))
            if len(found) > LISTED:
                w.append(f"- and {len(found) - LISTED} more")
            w.append("")
    print("\n".join(w))


if __name__ == "__main__":
    main(sys.argv[1:])
