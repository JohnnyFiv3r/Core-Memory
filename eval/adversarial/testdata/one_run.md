# Adversarial cases

- reader: **2 right, 0 wrong** of 2
- judge: **1 right, 1 wrong** of 2 pairs

The expected answers are ours (cases.toml), not a benchmark's.

## The reader, by probe

| Probe | Right | Wrong | Not read |
|---|---|---|---|
| decisions | 1 | 0 | 0 |
| in thread T1 | 1 | 0 | 0 |

## The judge, by thread

### T1: a budget that changes

- a Ann: “Alice sets the budget at ten thousand.” (decision)
- b Bo: “Alicia raises the budget to twelve thousand.” (not read)
- c Cy: “The budget is approved.” (not read)

- a → b: right. Said cause; second read: cause; right: cause
- b → c: **wrong**. Said none; right: repl
  - The raise replaces the first figure.
- also said, unscored: c → a: enabled-by
- names Alice = Alicia: linked
- “budget” linked to nothing: right
- written, ending currency: b repl c

## Every name link

- “Alice” (a) = “Alicia” (b)
