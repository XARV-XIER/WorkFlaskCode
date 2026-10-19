# Changelog

## 0.1.0 — first release candidate

- Setup wizard with back, quit, consent for plain-text key storage, live model discovery and a
  minimal model test.
- Providers: OpenRouter, OpenAI, Anthropic (native), and custom OpenAI-compatible endpoints.
- Error classification, bounded retries with backoff, and a fallback chain limited to
  model-specific failures.
- Coding agent with read, write, edit, delete, command and test tools inside four permission modes.
- Checkpoints with undo that leaves later user edits alone.
- `@chat`, `@codingagent`, non-blocking `@btw`, `@stop`, `@approve`, `@deny`.
- Persistent sessions, task ledger with validation-aware status, `@resume`, `@history`.
- Evidence-based audit with JSON reports; Git status, diff and branch commands.
- Project scaffolding (`@new`) and project metadata that never overwrites existing files.
- Known gaps are listed in `docs/STATUS.md`.
