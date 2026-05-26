from __future__ import annotations

import argparse


def add_async_jobs_command_surfaces(
    *,
    ops_sub: argparse._SubParsersAction,
    subparsers: argparse._SubParsersAction,
    legacy_help: str,
) -> None:
    """Register grouped + legacy async jobs command surfaces."""

    ops_sub.add_parser("jobs-status", help="Show canonical async queue/job status")

    ops_jobs_enqueue = ops_sub.add_parser("jobs-enqueue", help="Enqueue canonical async work")
    ops_jobs_enqueue.add_argument(
        "--kind",
        required=True,
        choices=["semantic-rebuild", "semantic-reconcile", "compaction", "dreamer-run", "neo4j-sync", "health-recompute"],
    )
    ops_jobs_enqueue.add_argument("--event-file", help="Optional JSON file for compaction event payload")
    ops_jobs_enqueue.add_argument("--ctx-file", help="Optional JSON file for compaction context payload")
    ops_jobs_enqueue.add_argument("--session-id", help="Optional session id shortcut for compaction context")
    ops_jobs_enqueue.add_argument("--run-id", help="Optional run id shortcut for compaction event")

    ops_jobs_run = ops_sub.add_parser("jobs-run", help="Run one bounded async jobs drain pass")
    ops_jobs_run.add_argument("--max-compaction", type=int, default=1)
    ops_jobs_run.add_argument("--max-side-effects", type=int, default=2)
    ops_jobs_run.add_argument("--no-semantic", action="store_true", help="Skip semantic rebuild even if queued")

    ops_dreamer_list = ops_sub.add_parser("dreamer-candidates", help="List Dreamer candidate queue records")
    ops_dreamer_list.add_argument("--status", choices=["pending", "accepted", "rejected"], help="Optional candidate status filter")
    ops_dreamer_list.add_argument("--limit", type=int, default=100)

    ops_dreamer_decide = ops_sub.add_parser("dreamer-decide", help="Accept/reject Dreamer candidate")
    ops_dreamer_decide.add_argument("--id", required=True, help="Dreamer candidate id")
    ops_dreamer_decide.add_argument("--decision", required=True, choices=["accept", "reject"])
    ops_dreamer_decide.add_argument("--reviewer")
    ops_dreamer_decide.add_argument("--notes")
    ops_dreamer_decide.add_argument("--apply", action="store_true", help="When accepting, apply canonical association")

    # hidden legacy aliases
    subparsers.add_parser("async-jobs-status", help=legacy_help)

    async_jobs_enqueue_parser = subparsers.add_parser("async-jobs-enqueue", help=legacy_help)
    async_jobs_enqueue_parser.add_argument(
        "--kind",
        required=True,
        choices=["semantic-rebuild", "semantic-reconcile", "compaction", "dreamer-run", "neo4j-sync", "health-recompute"],
    )
    async_jobs_enqueue_parser.add_argument("--event-file")
    async_jobs_enqueue_parser.add_argument("--ctx-file")
    async_jobs_enqueue_parser.add_argument("--session-id")
    async_jobs_enqueue_parser.add_argument("--run-id")

    async_jobs_run_parser = subparsers.add_parser("async-jobs-run", help=legacy_help)
    async_jobs_run_parser.add_argument("--max-compaction", type=int, default=1)
    async_jobs_run_parser.add_argument("--max-side-effects", type=int, default=2)
    async_jobs_run_parser.add_argument("--no-semantic", action="store_true")

    dreamer_candidates_parser = subparsers.add_parser("dreamer-candidates", help=legacy_help)
    dreamer_candidates_parser.add_argument("--status", choices=["pending", "accepted", "rejected"])
    dreamer_candidates_parser.add_argument("--limit", type=int, default=100)

    dreamer_decide_parser = subparsers.add_parser("dreamer-decide", help=legacy_help)
    dreamer_decide_parser.add_argument("--id", required=True)
    dreamer_decide_parser.add_argument("--decision", required=True, choices=["accept", "reject"])
    dreamer_decide_parser.add_argument("--reviewer")
    dreamer_decide_parser.add_argument("--notes")
    dreamer_decide_parser.add_argument("--apply", action="store_true")
