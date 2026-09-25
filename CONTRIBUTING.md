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

## Quality checks

Install `requirements-dev.txt` to get the pinned formatter and type checker.
Run the same checks as CI before requesting review:

```sh
python -m ruff format --check arcatom_codex
python -m ruff check arcatom_codex
python -m mypy
python -m unittest discover -s tests -v
python -m pip wheel --no-deps . --wheel-dir dist
```

Use four-space indentation, typed signatures, and concise English/Chinese
Google-style docstrings. Format at 80 columns; the lint limit is 100 terminal
cells. RPC objects may use server-defined JSON values; do not silence application
attribute errors with `Any`. Keep translation keys static and add their English
catalog entries. The internal `arcatom_codex` import namespace and legacy storage
paths remain stable for compatibility.

`ui.py` coordinates screens and delegates backend events, turn mutations,
history, navigation, home rows, and presentation to dedicated modules. Textual
`@on` handlers stay on the app class so the framework registers them correctly.
The typed access helpers describe this ownership; mixins do not create extra apps.

Keyboard tests distinguish input timing from rendering time. Use queued `Key`
events with explicit timestamps for rapid gestures: `pilot.press` waits for a
render after each key and does not model a rapid physical double press. The
native-driver ownership test checks the pinned Textual protocol sequences on each
CI platform. Headless tests cannot prove OS desktop switching works in a real
terminal; manually test macOS Spaces, Windows virtual desktops, and Linux desktop
shortcuts before claiming platform validation.

Release candidates require local user acceptance before tagging and publication.
