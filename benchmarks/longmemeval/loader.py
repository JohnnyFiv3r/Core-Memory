"""LongMemEval corpus loader.

The corpus is not vendored. Pass one of the LongMemEval JSON/JSONL files to
``load_longmemeval_corpus`` and the loader converts each question instance into
the shared BenchmarkAdapter contract shape.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.contracts import BenchmarkConversation, BenchmarkQA, BenchmarkTurn
from benchmarks.locomo.scoring import compute_evidence_recall, token_f1


class LongMemEvalLoaderError(Exception):
    pass


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _load_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise LongMemEvalLoaderError(
            f"longmemeval_corpus_not_found:{path}\n"
            "Download a LongMemEval data file separately and pass --longmemeval-corpus."
        )

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise LongMemEvalLoaderError(f"longmemeval_corpus_empty:{path}")

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LongMemEvalLoaderError(f"longmemeval_jsonl_error:line={lineno}:{exc}") from exc
            if not isinstance(row, dict):
                raise LongMemEvalLoaderError(f"longmemeval_jsonl_row_not_dict:line={lineno}")
            rows.append(row)
        return rows

    if isinstance(raw, list):
        rows_raw = raw
    elif isinstance(raw, dict):
        rows_raw = raw.get("data") or raw.get("instances") or raw.get("examples") or raw.get("questions")
        if rows_raw is None:
            rows_raw = list(raw.values())
    else:
        raise LongMemEvalLoaderError(f"longmemeval_unexpected_root:{type(raw).__name__}")

    if not isinstance(rows_raw, list):
        raise LongMemEvalLoaderError("longmemeval_rows_not_list")
    rows = [dict(row) for row in rows_raw if isinstance(row, dict)]
    if not rows:
        raise LongMemEvalLoaderError("longmemeval_no_instances")
    return rows


def _session_turns(raw_session: Any) -> list[dict[str, Any]]:
    if isinstance(raw_session, list):
        return [dict(t) for t in raw_session if isinstance(t, dict)]
    if isinstance(raw_session, dict):
        turns = raw_session.get("turns") or raw_session.get("messages") or raw_session.get("conversation") or []
        return [dict(t) for t in turns if isinstance(t, dict)]
    return []


def _session_id(raw_id: Any, index: int) -> str:
    value = _clean(raw_id)
    return value or f"session_{index}"


def _is_abstention(instance: dict[str, Any], question_id: str) -> bool:
    qtype = _clean(instance.get("question_type") or instance.get("category")).lower()
    return qtype == "abstention" or question_id.endswith("_abs")


def _instance_to_conversation(instance: dict[str, Any], *, index: int) -> BenchmarkConversation:
    question_id = _clean(
        instance.get("question_id")
        or instance.get("id")
        or instance.get("qa_id")
        or f"longmemeval_{index}"
    )
    question = _clean(instance.get("question"))
    if not question:
        raise LongMemEvalLoaderError(f"longmemeval_missing_question:index={index}")

    question_type = _clean(instance.get("question_type") or instance.get("category") or "unknown") or "unknown"
    haystack_sessions = _as_list(instance.get("haystack_sessions") or instance.get("sessions"))
    if not haystack_sessions:
        raise LongMemEvalLoaderError(f"longmemeval_missing_haystack_sessions:question_id={question_id}")

    session_ids = [_session_id(v, i) for i, v in enumerate(_as_list(instance.get("haystack_session_ids")))]
    dates = [_clean(v) for v in _as_list(instance.get("haystack_dates"))]
    answer_session_ids = [_clean(v) for v in _as_list(instance.get("answer_session_ids")) if _clean(v)]
    answer = _clean(instance.get("answer") or instance.get("expected_answer"))
    question_date = _clean(instance.get("question_date"))
    abstention = _is_abstention(instance, question_id)

    turns: list[BenchmarkTurn] = []
    for session_index, raw_session in enumerate(haystack_sessions):
        sid = session_ids[session_index] if session_index < len(session_ids) else f"session_{session_index}"
        timestamp = dates[session_index] if session_index < len(dates) else None
        for turn_index, raw_turn in enumerate(_session_turns(raw_session)):
            role = _clean(raw_turn.get("role") or raw_turn.get("speaker") or "user").lower()
            if role not in {"user", "assistant", "system"}:
                role = "user"
            content = _clean(raw_turn.get("content") or raw_turn.get("text") or raw_turn.get("message"))
            if not content:
                continue
            turns.append(
                BenchmarkTurn(
                    turn_id=f"longmemeval:{question_id}:{sid}:{turn_index}",
                    speaker=role,
                    role=role,
                    content=content,
                    timestamp=timestamp or None,
                    metadata={
                        "question_id": question_id,
                        "question_type": question_type,
                        "session_id": sid,
                        "session_index": session_index,
                        "turn_index": turn_index,
                        "has_answer": bool(raw_turn.get("has_answer")),
                    },
                )
            )

    if not turns:
        raise LongMemEvalLoaderError(f"longmemeval_no_turns:question_id={question_id}")

    labels = [question_type]
    if abstention:
        labels.append("abstention")
    qa = BenchmarkQA(
        qa_id=question_id,
        question=question,
        expected_answer=(None if abstention and not answer else answer or None),
        gold_evidence=answer_session_ids,
        category=question_type,
        bucket_labels=tuple(dict.fromkeys(labels)),
        metadata={
            "question_date": question_date,
            "answer_session_ids": answer_session_ids,
            "haystack_session_ids": session_ids,
            "is_abstention": abstention,
        },
    )

    return BenchmarkConversation(
        benchmark_name="longmemeval",
        conversation_id=f"longmemeval:{question_id}",
        session_id=f"longmemeval:{question_id}",
        turns=turns,
        qa_cases=[qa],
        metadata={
            "question_id": question_id,
            "question_type": question_type,
            "question_date": question_date,
            "haystack_session_count": len(haystack_sessions),
            "answer_session_ids": answer_session_ids,
            "is_abstention": abstention,
        },
    )


def load_longmemeval_corpus(path: Path | str, *, limit: int | None = None) -> list[BenchmarkConversation]:
    rows = _load_json_or_jsonl(Path(path))
    if limit is not None:
        rows = rows[: max(1, int(limit))]

    conversations: list[BenchmarkConversation] = []
    errors: list[str] = []
    for i, row in enumerate(rows):
        try:
            conversations.append(_instance_to_conversation(row, index=i))
        except LongMemEvalLoaderError as exc:
            errors.append(str(exc))

    if not conversations:
        suffix = f":{errors[0]}" if errors else ""
        raise LongMemEvalLoaderError(f"longmemeval_no_valid_instances{suffix}")
    return conversations


class LongMemEvalAdapter:
    @property
    def name(self) -> str:
        return "longmemeval"

    def load_conversations(self, **kwargs: Any) -> list[BenchmarkConversation]:
        corpus = kwargs.get("corpus") or kwargs.get("corpus_path") or kwargs.get("path")
        if not corpus:
            raise LongMemEvalLoaderError("longmemeval_corpus_path_required")
        return load_longmemeval_corpus(Path(corpus), limit=kwargs.get("limit"))

    def score_answer(self, *, qa: BenchmarkQA, prediction: str) -> float:
        if "abstention" in set(qa.bucket_labels):
            abstained = not _clean(prediction) or _clean(prediction).lower() in {
                "unknown",
                "i don't know",
                "not enough information",
                "not mentioned",
            }
            return 1.0 if abstained else 0.0
        if qa.expected_answer is None:
            return 0.0
        return round(token_f1(prediction, qa.expected_answer), 4)

    def score_evidence(self, *, qa: BenchmarkQA, retrieved_ids: list[str], k: int) -> dict[str, Any]:
        return compute_evidence_recall(
            gold_evidence=list(qa.gold_evidence or []),
            retrieved=[_clean(v) for v in retrieved_ids],
            ks=[k],
        )
