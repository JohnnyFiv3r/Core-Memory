<p align="center">
  <img src="docs/assets/core-memory-hero-banner.jpg" alt="Core Memory banner" />
</p>

# Core Memory 2.0 (in development)

Core Memory 2.0 is a ground-up rewrite in Rust. It turns any stream of source
events into durable, auditable memory records: it admits each event with its
exact bytes and a stable identity, cuts it into deterministic chunks, attaches
deterministic facets, assigns a type, optionally judges evidence-backed
relationships between chunks, and delivers versioned records to the sinks you
configure (memoriesQL, JSONL, webhooks).

The full chunk text is the memory. The engine never writes summaries or other
generated text into a record.

This branch is pre-release. Nothing from it is published to PyPI or crates.io
yet.

## Looking for Core Memory 1.x?

The Python implementation lives on the
[`v1.x`](https://github.com/JohnnyFiv3r/Core-Memory/tree/v1.x) branch and stays
on PyPI as the maintenance line:

```bash
pip install "core-memory<2"
```

Releases up to 1.1.2 are tagged on `v1.x`. Bug and security fixes for 1.x are
made there.

## License

Apache-2.0. See [LICENSE](LICENSE).
