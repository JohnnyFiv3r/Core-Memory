"""LoCoMo corpus loader.

Loads locomo10.json (10-sample public corpus) and converts it to
BenchmarkConversation objects suitable for the benchmark harness.

The corpus file must be obtained separately and placed at the path
passed to load_locomo_corpus(). It is not included in this repo due
to licensing — see benchmarks/locomo/README.md.

Corpus statistics (official public release):
  samples:   10
  QA items:  1986
  turns:     5882

Category exclusion: category 5 (adversarial/unanswerable) is excluded
from all official evaluation runs because 444/446 questions have
broken answer keys in the public corpus.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from benchmarks.contracts import BenchmarkConversation, BenchmarkQA, BenchmarkTurn

_OFFICIAL_CATEGORIES = {1, 2, 3, 4}
_DIA_ID_RE = re.compile(r"^D\d+:\d+$")

# Expected corpus statistics — validated on load to catch truncated files.
_EXPECTED_SAMPLES = 10
_EXPECTED_QA_MIN = 1980
_EXPECTED_TURNS_MIN = 5800


class LocomoLoaderError(Exception):
    pass


def _normalize_dia_id(raw: Any) -> str:
    """Return normalized dia_id string or raise on invalid input."""
    s = str(raw or "").strip()
    if not _DIA_ID_RE.match(s):
        raise LocomoLoaderError(f"invalid_dia_id:{s!r}")
    return s


def _normalize_turn(raw: dict[str, Any], *, sample_id: str, turn_index: int, session_index: int) -> dict[str, Any]:
    dia_id = _normalize_dia_id(raw.get("dia_id") or raw.get("turn_id") or "")
    speaker = str(raw.get("speaker") or raw.get("role") or "unknown").strip() or "unknown"
    text = str(raw.get("text") or raw.get("content") or "").strip()
    return {
        "dia_id": dia_id,
        "speaker": speaker,
        "text": text,
        "session_date_time": str(raw.get("session_date_time") or raw.get("timestamp") or ""),
        "img_url": str(raw.get("img_url") or ""),
        "blip_caption": str(raw.get("blip_caption") or ""),
        "turn_index": int(turn_index),
        "session_index": int(session_index),
        "sample_id": str(sample_id),
    }


def _normalize_qa(raw: dict[str, Any], *, sample_id: str) -> dict[str, Any] | None:
    cat_raw = raw.get("category") or raw.get("type") or raw.get("question_type")
    try:
        cat = int(cat_raw)
    except (TypeError, ValueError):
        return None

    question = str(raw.get("question") or "").strip()
    if not question:
        return None

    answer = str(raw.get("answer") or raw.get("expected_answer") or "").strip()

    # Gold evidence: list of dia_ids (raw strings from the corpus).
    gold_evidence: list[str] = []
    for g in raw.get("evidence") or raw.get("gold_evidence") or []:
        if isinstance(g, dict):
            d = str(g.get("dia_id") or g.get("turn_id") or "").strip()
        else:
            d = str(g or "").strip()
        if d:
            gold_evidence.append(d)

    return {
        "qa_id": str(raw.get("id") or raw.get("qa_id") or "").strip(),
        "question": question,
        "answer": answer,
        "gold_evidence": gold_evidence,
        "category": cat,
        "sample_id": sample_id,
    }


def _build_sessions(turns: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group turns into sessions by session_index."""
    sessions: dict[int, list[dict[str, Any]]] = {}
    for t in turns:
        idx = int(t.get("session_index") or 0)
        sessions.setdefault(idx, []).append(t)
    return [sessions[k] for k in sorted(sessions.keys())]


def load_locomo_corpus(path: Path | str) -> list[dict[str, Any]]:
    """
    Load the raw locomo10.json corpus file.

    Returns list of sample dicts with keys: sample_id, turns, qa_list.
    Validates basic corpus statistics.
    """
    path = Path(path)
    if not path.exists():
        raise LocomoLoaderError(
            f"locomo_corpus_not_found:{path}\n"
            "Place locomo10.json at that path. The corpus is available from the\n"
            "original LoCoMo authors: https://github.com/snap-research/locomo"
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LocomoLoaderError(f"locomo_corpus_json_error:{exc}") from exc

    # The corpus root may be a list of samples or a dict with a "data" key.
    if isinstance(raw, dict):
        samples_raw = raw.get("data") or raw.get("samples") or list(raw.values())
    elif isinstance(raw, list):
        samples_raw = raw
    else:
        raise LocomoLoaderError(f"locomo_corpus_unexpected_shape:{type(raw)}")

    if len(samples_raw) < _EXPECTED_SAMPLES:
        raise LocomoLoaderError(
            f"locomo_corpus_too_few_samples:expected>={_EXPECTED_SAMPLES},got:{len(samples_raw)}"
        )

    samples: list[dict[str, Any]] = []
    total_qa = 0
    total_turns = 0

    for i, sample_raw in enumerate(samples_raw):
        if not isinstance(sample_raw, dict):
            raise LocomoLoaderError(f"locomo_corpus_sample_not_dict:index={i}")

        sample_id = str(
            sample_raw.get("id") or sample_raw.get("sample_id") or sample_raw.get("conv_id") or f"sample_{i}"
        ).strip()

        # Turns may be flat list or grouped by session
        raw_turns: list[dict[str, Any]] = []
        raw_sessions = sample_raw.get("sessions") or []
        if raw_sessions:
            for s_idx, session in enumerate(raw_sessions):
                if isinstance(session, dict):
                    session_turns = session.get("turns") or session.get("utterances") or []
                elif isinstance(session, list):
                    session_turns = session
                else:
                    session_turns = []
                for t_idx, t in enumerate(session_turns):
                    if isinstance(t, dict):
                        t = dict(t)
                        if "session_index" not in t:
                            t["session_index"] = s_idx
                        raw_turns.append(t)
        else:
            flat = sample_raw.get("turns") or sample_raw.get("utterances") or []
            raw_turns = [dict(t) for t in flat if isinstance(t, dict)]

        turns: list[dict[str, Any]] = []
        for t_idx, t in enumerate(raw_turns):
            try:
                turns.append(_normalize_turn(t, sample_id=sample_id, turn_index=t_idx, session_index=int(t.get("session_index") or 0)))
            except LocomoLoaderError:
                continue  # Skip turns with malformed dia_ids

        qa_raw = sample_raw.get("qa_list") or sample_raw.get("questions") or sample_raw.get("qa") or []
        qa_list: list[dict[str, Any]] = []
        for q in qa_raw:
            if isinstance(q, dict):
                normalized = _normalize_qa(q, sample_id=sample_id)
                if normalized is not None:
                    qa_list.append(normalized)

        total_qa += len(qa_list)
        total_turns += len(turns)
        samples.append({"sample_id": sample_id, "turns": turns, "qa_list": qa_list})

    if total_qa < _EXPECTED_QA_MIN:
        raise LocomoLoaderError(
            f"locomo_corpus_too_few_qa:expected>={_EXPECTED_QA_MIN},got:{total_qa}"
        )
    if total_turns < _EXPECTED_TURNS_MIN:
        raise LocomoLoaderError(
            f"locomo_corpus_too_few_turns:expected>={_EXPECTED_TURNS_MIN},got:{total_turns}"
        )

    return samples


def locomo_samples_to_conversations(
    samples: list[dict[str, Any]],
    *,
    exclude_categories: set[int] | None = None,
    max_qa_per_sample: int | None = None,
) -> list[BenchmarkConversation]:
    """
    Convert raw LoCoMo samples to BenchmarkConversation objects.

    exclude_categories: defaults to {5} (broken answer keys).
    max_qa_per_sample:  cap QA items per conversation for smoke runs.
    """
    if exclude_categories is None:
        exclude_categories = {5}

    conversations: list[BenchmarkConversation] = []
    for sample in samples:
        sample_id = str(sample["sample_id"])
        session_id = f"locomo:{sample_id}"

        turns: list[BenchmarkTurn] = []
        for t in sample["turns"]:
            dia_id = str(t["dia_id"])
            turn_id = f"locomo:{sample_id}:{dia_id}"
            speaker = str(t["speaker"])
            # Normalise speaker → role mapping
            role = "assistant" if speaker.lower() in {"assistant", "ai", "bot"} else "user"
            turns.append(BenchmarkTurn(
                turn_id=turn_id,
                speaker=speaker,
                role=role,
                content=str(t["text"]),
                timestamp=str(t.get("session_date_time") or "") or None,
                metadata={
                    "dia_id": dia_id,
                    "session_index": int(t.get("session_index") or 0),
                    "turn_index": int(t.get("turn_index") or 0),
                    "img_url": str(t.get("img_url") or ""),
                    "blip_caption": str(t.get("blip_caption") or ""),
                    "sample_id": sample_id,
                },
            ))

        qa_items = [q for q in sample["qa_list"] if int(q.get("category") or 0) not in exclude_categories]
        if max_qa_per_sample is not None:
            qa_items = qa_items[:max_qa_per_sample]

        qa_cases: list[BenchmarkQA] = []
        for q in qa_items:
            cat = int(q.get("category") or 0)
            qa_cases.append(BenchmarkQA(
                qa_id=str(q.get("qa_id") or f"{sample_id}_q{len(qa_cases)}"),
                question=str(q["question"]),
                expected_answer=str(q["answer"]) or None,
                gold_evidence=list(q.get("gold_evidence") or []),
                category=str(cat),
                bucket_labels=(f"cat{cat}",),
                metadata={"sample_id": sample_id, "raw_category": cat},
            ))

        conversations.append(BenchmarkConversation(
            benchmark_name="locomo",
            conversation_id=sample_id,
            session_id=session_id,
            turns=turns,
            qa_cases=qa_cases,
            metadata={"sample_id": sample_id, "turn_count": len(turns), "qa_count": len(qa_cases)},
        ))

    return conversations
