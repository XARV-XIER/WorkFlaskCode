# Configuration

## Agent home

| Platform | Default location |
|---|---|
| Linux | `~/.workflaskcode/` |
| macOS | `~/Library/Application Support/WorkFlaskCode/` |
| Windows | `%LOCALAPPDATA%\WorkFlaskCode\` |

Override with `--home PATH` or the `WORKFLASKCODE_HOME` environment variable.

```text
config.toml          settings (no secrets)
credentials.json     only if you chose file storage (0600)
state/agent.db       sessions, messages, tasks, usage, checkpoints
state/prompt_history interactive history
backups/             pre-change copies used by @rollback
reports/             audit reports and exported transcripts
logs/commands.jsonl  command audit log
cache/               model catalogue cache
projects/            default location for @new projects
```

Project metadata lives in the project itself, in `.workflaskcode/` (`state.json`, `handoff.md`),
plus `PROJECT.md` in the project root. Existing files are never overwritten.

## config.toml

```toml
[general]
default_provider = "openrouter"
coding_model = "vendor/model-id"
chat_model = ""                  # empty = use coding_model
fallback_models = []             # "model-id" or "provider::model-id"
permission_mode = "approve_risky" # read_only | approve_all | approve_risky | restricted_auto
default_mode = "coding"          # chat | coding
setup_complete = true

[limits]
max_steps = 25                   # tool-loop steps per request
max_retries = 3                  # retries per model for transient failures
request_timeout_s = 120
command_timeout_s = 120
context_budget_chars = 120000    # compaction starts above this
max_btw_concurrency = 2
max_estimated_cost_usd = 0.0     # 0 = no budget

[git]
enabled = true

[privacy]
btw_sees_main_history = false

[providers.openrouter]
kind = "openrouter"
model = "vendor/model-id"
enabled = true

[providers.custom]
kind = "openai_compatible"
base_url = "https://llm.example.internal/v1"
model = "local-model"
```

Use `workflaskcode setup` or `@setup` to change most of this. `@permissions`, `@fallback` and
`@changemodel` save their changes too. Invalid values are rejected at load time with the setting
name in the message.

## Environment variables

| Variable | Effect |
|---|---|
| `WORKFLASKCODE_HOME` | Agent home directory |
| `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | Provider keys (take precedence) |
| `WORKFLASKCODE_KEY_<NAME>` | Key for a custom provider named `<name>` |
| `NO_COLOR` | Disable coloured output |
