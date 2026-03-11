# Full Read Audit

Timestamp (UTC): 2026-03-11T22:53:30.282457Z
Root: `/home/node/.openclaw/workspace/Core-Memory`

## Summary
- Total files scanned: 397
- Fully read (text): 391
- Binary skipped: 6
- Errors: 0
- Total lines read: 28724

JSON manifest: `docs/reports/full-read-audit-2026-03-11.json`

## Notes
- This audit opened each non-binary file and read it to EOF.
- Binary files were classified via NUL-byte sniff and marked skipped.
- .git and cache directories were excluded.
