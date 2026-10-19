# Implementation status

This file is the source of truth for what works. If a command or option is not listed as
implemented here, it is not implemented.

## Verified by automated tests (49 tests, offline)

The tests are in `tests/` of the source repository and are excluded from the release archive.

- Configuration: TOML round-trip, validation, malformed-file errors, dotted setting updates.
- Credentials: environment precedence, file permissions 0600 (POSIX), masking and redaction.
- Workspace: path traversal refused, symlink escape refused, `.git` writes refused.
- Providers: OpenRouter catalogue parsing (pricing, tools, vision), OpenAI-style tool calls,
  malformed arguments, Anthropic message translation and paginated model listing,
  error classification (auth, billing, quota, rate limit, unavailable, context overflow, overload).
- Routing: bounded retries with backoff and Retry-After, fallback on model-specific failures only,
  no fallback on account-wide failures, budget check before a request.
- Tools: permission modes, command classification, shell metacharacters treated as literals,
  timeouts that kill the process tree, edit uniqueness, read-only enforcement.
- Checkpoints: undo restores agent changes and reports conflicts with later user edits.
- Agent: tool loop, repeated-call refusal, cancellation, task state after a turn.
- Side chat: `@btw` runs without tools and does not change the main message history.
- Audit: evidence-based findings, secret masking in evidence, clean-project behaviour.
- Setup wizard: happy path, cancel saves nothing, declining file storage never writes a key.
- Scaffolding: the generated Python CLI project passes its own test.
- CLI: `--version`, `--help`, `where`, `doctor`, invalid-configuration error and exit code.

## Smoke-tested by hand (piped stdin, no network)

- Interactive loop, command dispatch, `@help`, `@where`, `@workspace`, `@permissions`,
  `@doctor`, `@new`, `@projects`, `@status`, `@history`, unknown-command suggestions.
- A coding request against an unreachable provider: bounded retries, then a `blocked` task with
  the failure saved and `@retry` suggested. No traceback is shown.
- `workflaskcode audit` on a file with a planted key and `shell=True`: both found, key masked,
  exit code 1 for high-severity findings.

## Implemented but NOT verified against live services

These depend on external services or optional packages that the build environment could not reach:

- Live calls to OpenRouter, OpenAI, Anthropic and custom endpoints. Request and response shapes
  follow the documented APIs and are covered by mocked tests, but have not been exercised live.
- `rich` output and `prompt_toolkit` editing, history and completion (the code paths exist and
  fall back to plain text when the packages are absent; they were not run here).
- OS keyring storage (`keyring` package).
- `ruff`, `mypy` and `bandit` integration inside `@audit`, `@types` and `@format`.

## Deviations from the brief

- **No Typer, Rich, prompt_toolkit or Pydantic in the core.** The build environment had no
  package index access. The core uses `argparse`, `tomllib`, `sqlite3`, `urllib` and dataclasses.
  Rich and prompt_toolkit are used when installed. Pydantic is replaced by explicit validation.
  Because the core has no runtime dependencies, installation has nothing to fail on.
- **Playwright browser testing is not implemented.** `@browser` says so and the wizard skips it.
- **No GitHub/PyPI publication has been performed.** The packaging metadata is ready.
- **No CI workflow** is included in this release.

## Not implemented in this release

These commands exist so that typos are caught, and they state that they are not implemented:
`@benchmark`, `@impact`, `@compare`, `@milestones`, `@release`, `@pr`, `@changelog`,
`@review-commit`, `@worktree`, `@migrate`, `@api-docs`, `@design-lock`, `@design-audit`,
`@design-diff`, `@browser`.

Also not implemented: plugins and skills, subagents and parallel writable agents, a Docker sandbox,
Playwright previews, Git commit and push automation, and multi-line input beyond a trailing `\`
continuation.

## Known limitations

- **Cancellation is cooperative.** A cancel request stops the loop before the next step and during
  retry backoff. An HTTP request already in flight finishes or hits its timeout first.
- **Command execution is not a sandbox.** Commands run as your user with a filtered environment,
  a timeout and process-group cleanup. Use `approve_all` or `read_only` on untrusted projects.
- **Token and cost figures** are shown only as the provider reports them. No estimates are made.
- **Free-tier quota** is not visible through the APIs used, so exhaustion is reported when it happens.
- **Context compaction** is deterministic: old turns are replaced by a summary of paths touched and
  earlier requests. Files on disk remain the source of truth.
- **Model capabilities** come from the provider catalogue when it reports them; unknown values
  are shown as `?`.
- **Windows** paths, process handling and credential behaviour follow the code paths but were not
  exercised on Windows in this build.
