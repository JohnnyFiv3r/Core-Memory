# Core Memory (mem-beads)

Persistent causal agent memory with lossless compaction.

## Canonical package

`tools/mem-beads/mem_beads/`

## Install

```bash
cd tools/mem-beads
pip install -e .
mem-beads --help
```

## Test

```bash
PYTHONPATH=tools/mem-beads python3 tools/mem-beads/test_edges.py
PYTHONPATH=tools/mem-beads python3 tools/mem-beads/test_e2e.py
```
