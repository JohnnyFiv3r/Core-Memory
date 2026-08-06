# Statement/request observation pack v1

This public synthetic pack is the PR-00E corpus contribution. It contains 25
evidence-bound conversation events: 13 with a preferred `statement` label and
12 with a preferred `request` label.

The cases exercise explicit preferences, reported incidents, hypotheses,
constraints, attributed explanations, corrections, rhetorical questions,
quoted imperatives, direct and indirect requests, permission questions,
conditional branches, clarification requests, prohibitions, and mixed
statement/request turns.

The gold contract is intentionally observation-first:

- a user or agent report is not upgraded into independently verified truth;
- a request or acknowledgement is not upgraded into completed work;
- quoted instructions are not treated as execution authorization;
- uncertainty, attribution, relative time, and missing evidence remain visible;
- every case permits an empty assertion array;
- association and revision output is unwarranted in this pack;
- every label, facet, proposition, and limitation resolves to exact evidence
  spans in the source event.

Inputs, gold, and adjudications are stored separately. The user-selected
`codex:gpt-5.6-sol-high` judge accepted 21 proposed records and recommended
four revisions. The human owner explicitly accepted all 25 final records.
Every adjudication binds both the proposed and accepted gold checksums.

The attempted external selector `openai:gpt-5.6-sol-high` correctly returned a
`blocked` run with zero records because no OpenAI API credential was configured.
The adjudications identify their completed execution as `codex_internal`; they
do not misrepresent the blocked provider attempt as a live API run.
