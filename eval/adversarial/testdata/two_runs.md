# Adversarial cases, 2 runs

| Run | Reader right | Judge right | Wrong | No verdict |
|---|---|---|---|---|
| adversarial-1 | 2 of 2 | 1 of 2 | 1 | 0 |
| adversarial-2 | 1 of 2 | 0 of 2 | 2 | 0 |

The expected answers are ours (cases.toml), not a benchmark's.

## The judge, pair by pair

Each run's verdict of its first read, as a's arrow to b; an arrow that ends currency and was written, after a `→`.

- T1 a → b (a budget that changes): **1 of 2**. cause; result. Right: cause
- T1 b → c (a budget that changes): **0 of 2**. none → repl; none → repl. Right: repl

Group beads (memories the judge joined): adversarial-1 1, adversarial-2 1.

## The reader: cases not right in every run

- **R1** (decisions) 1 of 2: “We will ship on Friday.” Said: decision; outcome. Right: decision
