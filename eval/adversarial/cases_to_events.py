"""Turn cases.toml into Core Memory SourceEvents, one per message.

    cases_to_events.py <cases.toml> <scope> <events.jsonl>

Each case of the reader stands alone, ten days from the next, so the judge
compares it with nothing. Each thread starts twenty days after the one
before: farther apart than the judge's seven-day window, however long a
thread runs, so no thread is judged with another.
"""

import datetime
import json
import sys
import tomllib

BASE = datetime.datetime(2023, 6, 5, 9, 0, tzinfo=datetime.timezone.utc)
READ_GAP = datetime.timedelta(days=10)
THREAD_GAP = datetime.timedelta(days=20)
LONGEST_THREAD = datetime.timedelta(days=12)


def event(scope, case_id, at, speaker, kind, role, text, thread):
    return {
        "scope": scope,
        "source_ns": "adversarial/v1",
        "external_id": case_id,
        "profile_id": "core.chat_transcript",
        "occurred_at": {"at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "precision": "second"},
        "actor": {"id": speaker, "kind": kind, "role": role},
        "media_type": "text/plain",
        "content": {"text": text},
        "structure": {"thread": thread},
        "parser_contract_version": "adversarial/1",
    }


def events(cases, scope):
    out = []
    for i, case in enumerate(cases.get("read", [])):
        out.append(
            event(
                scope,
                case["id"],
                BASE + i * READ_GAP,
                case.get("speaker", "Alex"),
                case.get("kind", "human"),
                case.get("role", "user"),
                case["text"],
                f"read/{case['id']}",
            )
        )
    start = BASE + (len(cases.get("read", [])) + 1) * READ_GAP
    for j, thread in enumerate(cases.get("thread", [])):
        begins = start + j * THREAD_GAP
        for message in thread["message"]:
            at = begins + datetime.timedelta(minutes=message["at"])
            if at - begins > LONGEST_THREAD:
                sys.exit(f"{message['id']}: a thread may run at most {LONGEST_THREAD.days} days")
            out.append(
                event(
                    scope,
                    message["id"],
                    at,
                    message["speaker"],
                    message.get("kind", "human"),
                    message.get("role", "user"),
                    message["text"],
                    message.get("conversation", thread["id"]),
                )
            )
    return out


def main(path, scope, out):
    with open(path, "rb") as f:
        cases = tomllib.load(f)
    written = events(cases, scope)
    ids = [e["external_id"] for e in written]
    if len(ids) != len(set(ids)):
        sys.exit("cases.toml: a message ID is used twice")
    with open(out, "w", encoding="utf-8") as f:
        for e in written:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"{len(written)} events")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
