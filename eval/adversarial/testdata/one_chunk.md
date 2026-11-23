# Adversarial cases

- reader: **2 right, 0 wrong** of 2
- judge: **1 right, 0 wrong** of 2 pairs, 1 with no verdict (never shown, or every reply rejected)

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

- a → b: right. Said cause; right: cause
- b → c: no verdict. Said -; right: repl
- names Alice = Alicia: **not linked**
- “budget” linked to nothing: right

## Every name link

None.
