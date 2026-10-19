# Troubleshooting

**`configuration problem: ...`** — `config.toml` has an invalid value. The message names the
setting. Fix it, or run `workflaskcode setup` to rewrite the file.

**`no API key for 'openrouter'`** — export the environment variable, or run `@setup` and choose
a storage option. `workflaskcode doctor` shows where each key is currently found.

**`network error`, then `task blocked`** — the provider is unreachable. The agent retried with
backoff. Check connectivity or a proxy, then run `@retry`.

**`rate_limit` or `overload` with a fallback** — the primary model was busy and a configured
fallback was used. `@usage` shows which model answered.

**`quota` or `billing`** — the account is out of credit or over its limit. Falling back does not
help, so the task stops. Top up the account or choose another provider with `@changemodel`.

**`tests FAILED` in `@tasks`** — the last test run failed after the agent's changes. Use
`@debug` with the failing output, or `@undo` to restore the agent's changes.

**`in_progress ... unverified`** — the agent changed files but no passing test run followed.
Run `@test`. If the project has no test command, add `"test_command": [...]` to
`.workflaskcode/state.json`.

**`refused: ...` in tool output** — a permission mode or the workspace boundary blocked the
action. `@permissions` shows the current mode.

**Garbled output while a task runs** — install the `ui` extra (`prompt_toolkit`) for
input that stays clean while output streams in.

**`command not found` from a test run** — the test runner is not on `PATH` in the agent's
filtered environment. Activate your virtual environment before starting the agent.
