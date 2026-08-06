# Goal/decision observation pack v1

This public synthetic pack is the PR-00F corpus contribution. It contains 25
evidence-bound conversation events: 13 with a preferred `goal` label and 12
with a preferred `decision` label.

The cases exercise explicit, measurable, tentative, conditional, attributed,
corrected, hierarchical, and constrained goals. Decision coverage includes
explicit, provisional, deferred, delegated, reported, corrected, conditional,
reversible, approval-only, rejected, and partially resolved choices.

The gold contract is intentionally observation-first:

- a goal is not upgraded into a completed result;
- a decision, approval, recommendation, or delegation is not upgraded into an
  executed action;
- conditional and provisional semantics remain unresolved where the evidence
  leaves them unresolved;
- third-party goals and decisions retain their attribution chain;
- relative dates remain relative when absolute temporal context is absent;
- every case permits an empty assertion array;
- association and revision output is unwarranted because no persisted semantic
  target is supplied;
- every label, facet, proposition, and limitation resolves to exact evidence
  spans in the source event.

Inputs, gold, and adjudications are stored separately. The user-selected
`codex:gpt-5.6-sol-high` judge required revisions to all 25 proposed records
because their initially intuitive facet names were outside the closed canonical
facet vocabulary. The accepted records use typed `custom` facets plus canonical
`commitment` and `correction` facets where warranted. Case 004 also removes an
unsupported `reflection` label alternative. The human owner explicitly accepted
all 25 revised records, and every adjudication binds both proposed and accepted
gold checksums.

The attempted external selector `openai:gpt-5.6-sol-high` correctly returned a
`blocked` run with zero records because no OpenAI API credential was configured.
The adjudications identify their completed execution as `codex_internal`; they
do not misrepresent the blocked provider attempt as a live API run.
