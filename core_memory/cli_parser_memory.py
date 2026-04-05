from __future__ import annotations

import argparse


def add_memory_command_surface(
    parent_subparsers: argparse._SubParsersAction,
    *,
    name: str,
    help_text: str,
    dest: str,
) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    """Attach canonical memory search/trace/execute command set to a parser tree."""
    memory_parser = parent_subparsers.add_parser(name, help=help_text)
    memory_sub = memory_parser.add_subparsers(dest=dest)

    mem_search = memory_sub.add_parser("search", help="Run canonical memory search")
    mem_search.add_argument("--query", default="")
    mem_search.add_argument("--intent", default="remember")
    mem_search.add_argument("--k", type=int, default=8)
    mem_search.add_argument("--explain", action="store_true")

    mem_trace = memory_sub.add_parser("trace", help="Run canonical memory trace")
    mem_trace.add_argument("--query", default="")
    mem_trace.add_argument("--k", type=int, default=8)
    mem_trace.add_argument("--anchor-ids", nargs="*")

    mem_exec = memory_sub.add_parser("execute", help="Run unified MemoryRequest execution")
    mem_exec.add_argument("--request", required=True, help="JSON object string or path to JSON file")
    mem_exec.add_argument("--explain", action="store_true")

    return memory_parser, memory_sub
