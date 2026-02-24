---
name: minimax
description: Delegate coding and development tasks to MiniMax M2.5 sub-agents. Use when offloading code generation, refactoring, debugging, or any dev work to save Claude tokens. Supports parallel sub-agent execution.
metadata:
  { "openclaw": { "emoji": "⚡" } }
---

# MiniMax M2.5 — Coding Delegation Skill

Delegate development tasks to MiniMax M2.5 via sub-agents, saving Claude Opus tokens for orchestration.

## Models Available

| Alias          | Model ID                  | Speed     | Use For                                    |
| -------------- | ------------------------- | --------- | ------------------------------------------ |
| `minimax`      | `minimax/MiniMax-M2.5`    | ~60 tps   | Complex coding, multi-file refactors       |
| `minimax-fast` | `minimax/MiniMax-M2.5-highspeed` | ~100 tps  | Simple tasks, quick generations, boilerplate |

## When to Delegate to MiniMax

**Good fits (use MiniMax):**
- Code generation (new files, features, functions)
- Refactoring existing code
- Writing tests
- Debugging / fixing errors (with clear context)
- Documentation generation
- Config file creation
- Single-file or well-scoped multi-file changes
- Boilerplate / scaffolding

**Keep on Claude Opus:**
- Orchestration across multiple sub-agents
- Complex reasoning about architecture decisions
- Tasks requiring personality / conversation
- Tasks needing image understanding
- Anything requiring deep context of the user's preferences and history

## How to Delegate

### Single Task
```
sessions_spawn(
  task: "Your detailed coding prompt here. Include all file contents and context needed.",
  model: "minimax/MiniMax-M2.5",
  label: "descriptive-label"
)
```

### Parallel Tasks
Spawn multiple sub-agents simultaneously — they run independently:
```
sessions_spawn(task: "Task A...", model: "minimax/MiniMax-M2.5", label: "feature-auth")
sessions_spawn(task: "Task B...", model: "minimax/MiniMax-M2.5", label: "feature-api")
sessions_spawn(task: "Task C...", model: "minimax/MiniMax-M2.5-highspeed", label: "write-tests")
```

Use `minimax-fast` (highspeed) for the simpler tasks in the batch.

### Monitoring
- Sub-agents auto-announce when done — don't poll in a loop
- Check on demand: `subagents(action: "list")`
- Steer if needed: `subagents(action: "steer", target: "label", message: "...")`
- Kill if stuck: `subagents(action: "kill", target: "label")`

## Writing Good Delegation Prompts

MiniMax M2.5 is a strong coder but has no persistent context. Every spawn is a blank slate.

**Always include in the task prompt:**
1. **Full file contents** that need to be read or modified (don't reference paths it can't access)
2. **Clear deliverable** — what files to create/modify, exact expected output
3. **Constraints** — language, framework, style conventions
4. **Where to write** — explicit file paths

**Template:**
```
You are a coding assistant. Your task: [description]

Here are the current files:

### path/to/file.swift
```swift
[full file contents]
`` `

[Repeat for each relevant file]

Requirements:
- [Specific requirements]
- [Style/convention notes]

Output the complete modified files with their paths.
```

## API Details
- Provider: MiniMax (Anthropic-compatible API)
- Base URL: https://api.minimax.io/anthropic
- Context window: 204,800 tokens
- Supports: streaming, tool use, thinking/reasoning
- No image input support
- Subscription: Codingplan Starter tier (monthly)

## Limits & Cost Awareness
- Starter tier has usage limits — prefer `minimax-fast` for trivial tasks
- Batch related small tasks into one prompt when possible
- For very large files (>50k tokens), summarize irrelevant sections

## Lessons Learned
- MiniMax is Anthropic-API-compatible, so it works natively with OpenClaw's `anthropic-messages` API type
- Sub-agents on MiniMax can't access the filesystem — include all context in the prompt
- Don't delegate tasks that need back-and-forth conversation — those need the main session
- For iOS/watchOS work: include project.yml, relevant Swift files, and build constraints in the prompt
