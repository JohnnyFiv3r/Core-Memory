# PR-Ready Checklist: core-graph-archive-retrieval

## Scope & Diff Hygiene
- [x] Branch rebased from `master@8c3f9f4`
- [x] Gap closure sequence completed (#1–#7)
- [x] No runtime-only artifacts committed from `memory/.beads/*`

## Correctness
- [x] Archive index rebuild path exists and tested
- [x] Graph materialization from events exists and tested
- [x] Semantic lookup supports deterministic fallback
- [x] Active semantic top-K cache enforced with deactivation trail
- [x] Centrality included as soft factor in traversal ranking
- [x] Intent router is soft (non-brittle), with fallback pathway
- [x] Chain dedupe/diversity + confidence included in response
- [x] Structural inference/backfill hardened with strict gates
- [x] Citation payload enriched with grounded-role/confidence

## Testing
- [x] New tests added for each gap closure area
- [x] End-to-end selected suite run completed
- [x] Latest recorded run: **44 tests passing**

## Docs
- [x] `docs/graph_memory.md` present
- [x] `docs/pr_merge_package_graph_archive.md` added
- [x] `docs/pr_release_notes_core_graph_archive.md` added

## Merge Notes
- [ ] Open PR from `core-graph-archive-retrieval` into `master`
- [ ] Include rollout commands and risk notes from merge package
- [ ] Optional: squash strategy decision (keep staged history vs squash)

## Suggested PR Title
`Graph/archive retrieval stack (R1–R4) + gap closures #1–#7`

## Suggested PR Description
Use `docs/pr_release_notes_core_graph_archive.md` as the primary body, and append `docs/pr_merge_package_graph_archive.md` risk/rollout section.
