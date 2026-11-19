"""Turn one LoCoMo conversation into Core Memory SourceEvents, one per turn.

Each turn is one chat message. A session gives only its start time to the
minute, so turns are placed one second apart from it, in their order.
Image captions are not part of what was said and are left out.
"""
import datetime
import json
import sys

def session_start(text):
    # "1:56 pm on 8 May, 2023"
    return datetime.datetime.strptime(text.strip(), "%I:%M %p on %d %B, %Y").replace(
        tzinfo=datetime.timezone.utc)

def main(path, sample_id, scope, out):
    data = json.load(open(path))
    conv = next(c for c in data if c["sample_id"] == sample_id)["conversation"]
    sessions = sorted(
        (int(k.split("_")[1]) for k in conv if k.startswith("session_") and not k.endswith("date_time")))
    n = 0
    with open(out, "w") as f:
        for s in sessions:
            start = session_start(conv[f"session_{s}_date_time"])
            for i, turn in enumerate(conv[f"session_{s}"]):
                at = start + datetime.timedelta(seconds=i)
                event = {
                    "scope": scope,
                    "source_ns": f"locomo/{sample_id}",
                    "external_id": turn["dia_id"],
                    "profile_id": "core.chat_transcript",
                    "occurred_at": {"at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "precision": "second"},
                    "actor": {"id": turn["speaker"], "kind": "human", "role": "user"},
                    "media_type": "text/plain",
                    "content": {"text": turn["text"]},
                    "structure": {"thread": f"{sample_id}/session_{s}"},
                    "parser_contract_version": "locomo/1",
                }
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                n += 1
    print(f"{n} events from {len(sessions)} sessions")

if __name__ == "__main__":
    main(*sys.argv[1:5])
