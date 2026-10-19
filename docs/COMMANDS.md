# Command reference

Commands start with `@`. Anything else is sent to the agent as a request. Unknown commands get a
suggestion. Status key: **Yes** = implemented and tested offline; **Partial** = implemented with a
stated dependency on an external tool or live service; **No** = not implemented.

## General

| Command | Status | Purpose |
|---|---|---|
| `@help` | Yes | List commands by group |
| `@setup` | Yes | Reopen the configuration wizard (reloads config on success) |
| `@status` | Yes | Mode, model, state, last task and its validation |
| `@where` | Yes | Agent home, config, database, project and metadata paths |
| `@workspace` | Yes | Project root, authorized roots, permissions, Git branch, stack |
| `@projects` | Yes | Registered projects; the current one is starred |
| `@open <path>` | Yes | Validate and switch project; starts a new session |
| `@new <name> [parent]` | Yes | Scaffold a Python CLI project (with a passing test) and open it |
| `@resume [session-id]` | Yes | Restore the last or a named session for this project |
| `@history [text]` | Yes | List sessions, or search messages |
| `@clear` | Yes | Clear the screen; data is kept |
| `@exit` | Yes | Cancel any running task, save state and quit |
| `@doctor` | Yes | Configuration, keys (masked source only), dependencies, workspace |

## Models and providers

| Command | Status | Purpose |
|---|---|---|
| `@models [refresh] [filter]` | Yes | List the live catalogue (cached 24 h); show price, context, tools, vision |
| `@changemodel <id> [provider]` | Yes | Test the new model first; keep the old one on failure; preserve project state |
| `@testmodel [id]` | Yes | Minimal request to check a model |
| `@providers` | Yes | Configured providers, default marker, and key source (never the key) |
| `@usage` | Yes | Requests, successes, tokens and provider-reported cost |
| `@limits` | Yes | Limits in effect and session spend |
| `@fallback [list, add, remove, clear]` | Yes | Manage the fallback chain (`provider::model` for another provider) |
| `@retry` | Yes | Run the last request again |

## Modes and control

| Command | Status | Purpose |
|---|---|---|
| `@chat` | Yes | Read-only mode with the chat model (no tools) |
| `@codingagent` | Yes | Coding mode with tools inside the permission policy |
| `@btw <question>` | Yes | Side question: read-only, own history, runs in parallel with the main task |
| `@stop [btw]` | Yes | Cancel the main task, or only side questions |
| `@approve` / `@deny` | Yes | Answer a pending permission request |

## Planning and state

| Command | Status | Purpose |
|---|---|---|
| `@plan <goal>` | Yes | Ask the model for a numbered plan and record each step as a pending task |
| `@tasks` | Yes | Task ledger for this session, with validation and next action |
| `@checkpoint [label]` | Yes | Start a labelled checkpoint for grouping the agent's writes |
| `@rollback [id or latest]` | Yes | Restore files the agent changed; files you edited since are left alone |
| `@undo` | Yes | Alias for `@rollback latest` |
| `@permissions [mode]` | Yes | Show or set the permission mode (saved to config) |
| `@compact` | Yes | Summarize older turns to free context |
| `@export [path]` | Yes | Write the session transcript as Markdown |

## Code intelligence

| Command | Status | Purpose |
|---|---|---|
| `@audit [--json] [--no-tools]` | Yes | Evidence-based static audit; report saved under reports/. Ruff and Bandit run if installed (Partial) |
| `@security` | Yes | Audit restricted to security findings |
| `@test` | Yes | Run the project's test command (detected or set in `.workflaskcode/state.json`) |
| `@search <text>` | Yes | Case-insensitive search across project files |
| `@todo` | Yes | List TODO and FIXME markers |
| `@deps` | Yes | Declared dependencies from pyproject, requirements.txt and package.json |
| `@format` | Partial | `ruff format .` after approval (needs ruff) |
| `@types` | Partial | `mypy .` (needs mypy) |
| `@deadcode` | Partial | `ruff check` for unused imports, redefinitions and locals (needs ruff) |
| `@explain [path]` | Yes | Model explains a file or the project (read-only) |
| `@review [path]` | Yes | Model reviews a file, or the working-tree diff (read-only) |
| `@explain-diff` | Yes | Model explains uncommitted changes (read-only) |
| `@architecture` | Yes | Model describes the architecture from project files (read-only) |
| `@debug <symptom>` | Yes | Agentic: reproduce, find root cause, minimal fix, run tests |
| `@refactor <target>` | Yes | Agentic: behaviour-preserving refactor, then tests |
| `@generate-tests <path>` | Yes | Agentic: tests in the project's framework, then run them |
| `@docs <path>` | Yes | Agentic: accurate docstrings, behaviour unchanged |

## Git

| Command | Status | Purpose |
|---|---|---|
| `@diff [--staged] [path]` | Yes | Uncommitted changes (stat and patch, truncated) |
| `@branch [list, name, switch name]` | Yes | List, create or switch branches. Never pushes or deletes |

## Not implemented in this release

`@benchmark`, `@impact`, `@compare`, `@milestones`, `@release`, `@pr`, `@changelog`,
`@review-commit`, `@worktree`, `@migrate`, `@api-docs`, `@design-lock`, `@design-audit`,
`@design-diff`, `@browser`. Each prints that it is not implemented. See `docs/STATUS.md`.

## Entering a request

Any line that does not start with `@` becomes a request. In coding mode it can read, write and
run tests. Lines ending with `\` continue onto the next line.
