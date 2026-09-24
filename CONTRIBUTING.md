# Contributing

The default branch is `master`. Create a feature branch and open a pull request against it.

## Local setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python -m unittest discover -s tests -v
```

The offline tests use synthetic sessions and do not require a Codex account. Use `--demo` when collecting screenshots. Never include real conversations, tokens, authentication files, personal skills, or private project paths in commits or issue attachments.

Real integration checks in `scripts/live_check.py` require a local Codex login. Flags that send prompts consume model usage. They are not run in CI.

Changes to command behavior should include regression coverage for cancellation, backend failures, and session isolation where applicable. Keep native Codex handoffs visibly identified and preserve the backend's approval and trust decisions.

Windows uses `.venv\Scripts\python.exe`. Keep platform-specific imports inside their platform branches. UI strings go through `i18n.tr`; keep `locales/en.json` complete and never translate user/model content or protocol identifiers.
