# Contributing

1. Create a virtual environment and install the development extras: `pip install -e ".[dev]"`.
2. Run the tests: `python -m unittest discover -s tests -t .`
3. Keep the core dependency-free. New third-party packages need a clear reason in `docs/STATUS.md`.
4. Every new command must call real code or say it is not implemented. Do not add placeholder output.
5. Any change that touches execution, paths, credentials or permissions needs a test that proves the
   refusal path, not only the success path.
6. Keep documentation in step with behaviour. `docs/COMMANDS.md` and `docs/STATUS.md` are checked
   against the code during review.

Please open an issue before large changes.
