# Security model

WorkFlaskCode is built on the assumption that model output is untrusted input, and that a project
may contain hostile text (for example a README that tries to instruct the agent).

## Credentials

- Keys come from environment variables first, then the OS keyring, then a credentials file.
- The credentials file is written with mode 0600 on POSIX. Storing a key there needs an explicit
  `yes` at the wizard, which states that the key is kept in clear text.
- Keys never go into `config.toml`, project files, logs, the state database or reports.
- Error text passes through redaction: known keys and common key shapes (`sk-`, `sk-or-v1-`,
  `sk-ant-`, `AKIA...`, bearer tokens) are replaced with `[REDACTED]`.
- `@providers` shows where a key comes from, never its value. `@doctor` shows the masked source.

## Workspace boundary

Every path the agent uses is resolved with symlinks followed, and must fall inside an authorized
root (the project directory). `..` escapes and symlinks that point outside are refused. Writes
inside `.git` are refused. Only the project root is authorized by default; additional roots are
an API-level option, not a command.

## Permission modes

| Mode | Reads | Writes | Test runners | Other commands | Deletes |
|---|---|---|---|---|---|
| `read_only` | Yes | No | No | No | No |
| `approve_all` | Yes | Ask | Ask | Ask | Ask |
| `approve_risky` (default) | Yes | Yes | Yes | Ask | Ask |
| `restricted_auto` | Yes | Yes | Yes | Refused | Refused |

Commands are classified before they run:

- **deny**: privilege escalation, disk and system tools (`sudo`, `mkfs`, `dd`, `shutdown`, ...),
  and targets such as `/` or `~`. Always refused.
- **allow**: read-only utilities and read-only Git subcommands, plus `ruff` analysis and `bandit`.
- **test**: `pytest`, `python -m unittest`, `cargo test`, `go test`, `npm test`.
- **ask**: everything else, including `git commit`, `rm`, `ruff format`, arbitrary Python.

Denial and approval requests are shown in the interface. `@approve` and `@deny` answer them.
A request that is not answered is denied when the turn is cancelled.

## Command execution

- Model text is split with `shlex` and run as an argument vector. No shell is involved, so
  `;`, `&&`, `|`, `>` and `$(...)` are inert literal arguments. This is tested.
- The child environment keeps a short allow-list of variables and drops any name containing
  KEY, TOKEN, SECRET, PASSWORD, PASS, CREDENTIAL or COOKIE.
- Each command has a timeout (default 120 s). On timeout the whole process group is killed.
- Output is capped at 12,000 characters.
- Every command is appended to `logs/commands.jsonl` with its argv, exit code and working directory.

This is not a sandbox. A permitted test runner executes project code with your privileges. Use
`approve_all` or `read_only` for projects you have not reviewed.

## Undo and your changes

Before the agent writes a file, the previous content is copied to `backups/`. `@rollback` restores
only files whose current content still matches what the agent wrote. If you edited a file after
the agent, it is reported as a conflict and left unchanged.

## Audit limits

`@audit` is pattern analysis with evidence from concrete matches and, where installed, real tool
output. A clean report means those patterns were not found. It does not mean the project is
secure, and the report says so. Do not use it as a compliance attestation.

## Side questions

`@btw` runs without tools and cannot change files or run commands. By default it receives only a
short status summary of the main task. `privacy.btw_sees_main_history = true` also passes the last six
main messages.

## Reporting vulnerabilities

See `SECURITY.md` in the repository root.
