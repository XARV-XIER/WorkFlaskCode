I know this thingy is vibecoded im sorry for it


# WorkFlaskCode

**Your code. Your models. Your workflow.**

WorkFlaskCode is an open-source, terminal-first AI software engineering agent written in Python.
You bring your own provider and API key, pick a model from the provider's live catalogue, and use
the agent to inspect, change, test and debug a project that lives wherever you tell it to.

- No web dashboard, no hosted backend, no shared API key.
- Nothing is sent to a provider until you start a request that needs one.
- Works with OpenRouter, OpenAI, Anthropic, and any OpenAI-compatible endpoint.
- Core runs on the Python standard library. Rich and prompt_toolkit are optional enhancements.

## Requirements

- Python 3.11 or newer (Linux, macOS, Windows)
- Git (optional, for Git integration)
- An API key for at least one provider

## Install (from a local checkout)

```bash
python -m pip install -e .                 # core (no third-party dependencies)
python -m pip install -e ".[ui]"           # adds rich + prompt_toolkit for nicer output and editing
python -m pip install -e ".[keyring]"      # optional OS credential store
python -m pip install -e ".[dev]"          # pytest + ruff for development
```

PyPI publication is not done yet. See `docs/RELEASE.md` for the packaging steps.
Without installing, you can also run `PYTHONPATH=src python -m workflaskcode`.

## First run

```bash
workflaskcode setup       # guided wizard: provider, key, model, permissions, Git
workflaskcode doctor      # verify configuration, keys, dependencies and workspace
workflaskcode             # open the interactive agent in the current directory
workflaskcode --project ~/code/myapp
```

The wizard can be cancelled at any step (`q`), goes back with `b`, and saves nothing until
you confirm the final summary.

## Everyday use

```text
code> @models refresh          # fetch the live model catalogue
code> @changemodel vendor/x    # validate a model, then switch without losing project state
code> add input validation to the signup form and write tests
code> @btw why did we pick SQLite here?       # side question; the main task keeps running
code> @test                    # run the project's test command
code> @diff                    # see what changed
code> @undo                    # undo the agent's own changes, keeping your unrelated edits
code> @audit                   # evidence-based audit report, saved as JSON
```

Type `@help` for the full list. `docs/COMMANDS.md` documents every command and its status.

## Modes and permissions

- `@chat` is read-only conversation with the chat model. It cannot write files or run commands.
- `@codingagent` enables tools inside the permission policy (default mode).
- Permission modes: `read_only`, `approve_all`, `approve_risky` (default), `restricted_auto`.
  See `docs/SECURITY.md`.

## Documentation

| Document | Contents |
|---|---|
| `docs/COMMANDS.md` | Command reference with implementation status |
| `docs/PROVIDERS.md` | Providers, model discovery, custom endpoints, fallback chains |
| `docs/SECURITY.md` | Key storage, permissions, command execution model, audit limits |
| `docs/CONFIGURATION.md` | `config.toml` reference, paths per platform, environment variables |
| `docs/STATUS.md` | Feature status, deviations from the brief, known limitations |
| `docs/TROUBLESHOOTING.md` | Common problems and fixes |
| `docs/RELEASE.md` | Packaging, versioning and publishing steps |

## Development

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -t .
python -m compileall -q src
```

The test suite lives in `tests/` in the source repository. It is deliberately excluded from the
release archive. Automated tests never call a live provider; they use a scripted HTTP transport.

## License

MIT. See `LICENSE`.
