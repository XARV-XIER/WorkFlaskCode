# Release and packaging

## Build and check locally

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
python -m pip install dist/workflaskcode-0.1.0-py3-none-any.whl
workflaskcode --version
```

## Publish (when ready)

1. Run the test suite and `python -m compileall -q src`.
2. Update `version` in `pyproject.toml`, `src/workflaskcode/__init__.py` and `CHANGELOG.md`.
3. Tag the commit: `git tag v0.1.0 && git push --tags`.
4. Upload: `python -m twine upload dist/*` (use a PyPI API token from the environment).
5. Verify in a clean virtual environment: `python -m pip install workflaskcode==0.1.0`.

Nothing in this repository publishes automatically.
