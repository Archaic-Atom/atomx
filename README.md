# Arcatom Codex

[简体中文](README.zh-CN.md) · [MIT license](LICENSE)

A keyboard-first terminal workspace for local Codex sessions, with a compact Archaic-Atom header, grouped sessions, live usage, subagents, image attachments and configurable colors. The interface defaults to English; choose **F2 → Language → 简体中文 → Save** to switch.

## Install and run

Requires Python 3.11+ and an installed, signed-in Codex CLI. The protocol integration is verified against `codex-cli 0.156.1`.

macOS / Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps .
codex login
.venv/bin/arcatom
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe -m pip install --no-deps .
codex login
.venv\Scripts\arcatom.exe
```

On macOS / Linux, `./arcatom` runs directly from this checkout. To install a user-local development launcher, run `python3 scripts/install_launcher.py`; it does not modify your shell configuration. On Windows, that script installs this package into the Python environment used to run it.

```sh
arcatom --cwd /path/to/project
arcatom --demo   # Offline fixtures; no model requests
arcatom --check  # Read-only local connection check
```

The default directory is the launch directory, configurable in Settings. Existing sessions keep their own working directories.

Multiple updated Arcatom windows with the same local user, Codex installation and `CODEX_HOME` share one backend. Opening the same session subscribes to its live messages and task state; closing a window leaves the backend running. Unsent drafts remain local to each window. Restart all older Arcatom windows once after updating to join the shared service. Independently launched Codex servers are not synchronized by this connection.

macOS/Linux use a private local Unix socket; Windows uses the official `app-server daemon` and `proxy` transport and requires a complete Codex installation. Shared transport was tested locally on macOS; Windows/Linux still need platform testing.

Long conversations open immediately and load their newest 40 items in the background. Scroll to the top and press `↑`, or select **Load earlier messages**, to fetch another page. Reopening a loaded session reuses its rendered messages. A failed history load can be retried with `Ctrl+R`.

## Keyboard workflow

| Where / action | Keys |
| --- | --- |
| Home: start a new session | Empty search + `Enter` |
| Home: select and resume history | `↑` / `↓`, then `Enter`; typing filters sessions |
| Choose a directory for a new session | `Ctrl+N`, `/new`, or the New session button |
| Home: permanently delete selected history | `Ctrl+X` (no second confirmation) |
| Editor: recall sent prompts | `↑` on the first line, then `↑` / `↓`; down past the latest restores the unsent draft |
| Editor: browse the conversation | `Esc` leaves editing and focuses the transcript |
| Transcript: scroll / jump | `↑` / `↓`; two quick `↑` presses jump to top, two quick `↓` presses jump to bottom |
| Transcript: return to input | At the bottom, `↓` selects the input; typing or `Enter` enables editing without sending |
| Return home | `Esc` while browsing, or `←` while browsing / input is empty |
| Return directly to editing | Start typing anywhere in the conversation, or `Ctrl+L` / `F6`; the first character is preserved |
| Send / newline | `Enter` / `Ctrl+J` (also `Shift+Enter` in supporting terminals) |
| Copy selected text | Select, then `Ctrl+C`, `F3` / `Ctrl+Shift+C`; forwarded `Cmd+C` also works |
| Copy latest reply when nothing is selected | `F3`, `Ctrl+Shift+C` or `/copy` |
| Paste text or image | `Ctrl+V` |
| Attach image file / remove pending image | `F4` / `F8` |
| Slash commands | `/`, then `↑↓`, `Tab` to complete, `Enter` to run, `Esc` to dismiss |
| Model / reasoning effort | `/model` / `/reasoning` |
| Subagents and processes | `Ctrl+T`, then `↑↓` and `Enter` for details |
| Settings / colors / language | `F2`, `/settings`, `/palette` |
| Usage / refresh | `Ctrl+U` / `Ctrl+R` |
| Interrupt current turn | `Ctrl+C` when no text is selected |
| Quit | `Ctrl+Q` or `/quit` |

The input is editable on entry. **Esc → browse → Esc → home** preserves your draft. PageUp/PageDown remain optional scrolling shortcuts. Tab and Shift+Tab navigate controls, including settings and dialogs. The larger home input sits below the session list. Home arrows move a single focus through buttons, sessions and input; ↑ from the input selects the last session, and ↓ from the last session returns to the input. Mouse selection never copies automatically; clipboard copy does not interrupt a running turn when text is selected.

The home list has separate colored **Needs your attention**, **Working**, and **History** section headings. Each session occupies one row: title and working directory, plus a reply/task preview on wider terminals. Live approval/question flags take priority. Sessions created or completed in this app, and sessions with drafts or pending images, await input. Old idle sessions remain history. Only statuses available from this app's backend can be shown; another Codex process may own work this backend cannot observe or control.

## Appearance and usage

Shell commands occupy one status line in the conversation, even when they contain multiline scripts. Long commands are ellipsized; press `Ctrl+T`, select a command and press `Enter` to see the complete script and output.

The default appearance is **Graphite + Copper**. Settings offers Warm sand, Midnight, Forest, Graphite and Paper palettes, accent swatches, language, compact layout, output following and the new-session directory. Use ↑/↓ to move between settings, Enter to open a choice, ↑/↓ to choose, and Enter to confirm. Esc closes the choice first, then leaves settings; Ctrl+S saves. Directory editing also starts with Enter, and Esc cancels that field edit. Colors preview immediately; Cancel restores the previous appearance. Language applies on Save. User messages have a separate gray background; changing the interface language does not translate user messages, model replies, paths or server data.

Usage stays in the status bar: time, cumulative session tokens, remaining context, remaining rate-limit windows and model. Missing values are hidden or shown as `—`; cached input is not counted twice. Session totals can overlap through inherited history and are not billing totals. Account limits refresh every minute; `Ctrl+R` refreshes manually.

`/statusline` controls fields, `/title` controls the terminal title, and `/theme` controls code highlighting. Preferences live in `~/.config/arcatom/preferences.json` on all platforms. Demo mode does not persist preferences. The app respects `NO_COLOR`; remove that environment variable if you want colors.

## Clipboard and images

Fonts and font size are controlled by your terminal. For iTerm2’s intercepted Cmd+C shortcut, see [terminal keyboard setup](docs/terminal.md).

Text copy works in both real and demo sessions, uses the system clipboard first, and sends OSC 52 only as a fallback. Success is shown after the system clipboard accepts the text; an unavailable system clipboard produces a terminal-fallback notice. macOS uses its built-in clipboard tools; Windows uses PowerShell; Linux needs `wl-clipboard` for Wayland or `xclip` for X11. Image clipboard access uses Pillow and the available desktop clipboard. Headless/SSH environments may have no desktop clipboard. Use **F4** to attach a readable image file when clipboard access is unavailable or the terminal intercepts Ctrl+V.

Pasted images appear as pending attachments and are sent only after Enter. Attachments belong to their session, survive switching sessions, and are restored after a send failure. Image snapshots remain in `~/.cache/arcatom/attachments/` so Codex history can reference them. F8 removes a pending reference, not the cached file; remove that cache manually when you no longer need the historical images. Support for interpreting images depends on the selected model.

## Codex integration

**Approve for me is enabled by default.** In **F2 → Approve for me**, turn the switch off and Save to use manual approvals. The preference sets `approvalPolicy=on-request` and selects Codex's `auto_review` or `user` reviewer for new/resumed sessions and subsequent turns. It preserves sandbox boundaries and global Codex configuration. Active turns and already-open approval dialogs are not retroactively approved. Backend restrictions still apply; a rejected setting prevents the new request from being sent. See [official auto-review documentation](https://learn.chatgpt.com/docs/sandboxing/auto-review).

The client connects to a shared local `codex app-server`, retaining local login, configuration, sandboxing and approvals. Approval dialogs default to decline; unsupported interactive requests are declined explicitly. Returning home does not stop work. Quitting disconnects this window while the shared backend continues its tasks.

The menu contains **72 entries** including aliases: 48 handled in the app and 24 marked **[native]**. See [command coverage](docs/commands.md). Native commands temporarily hand the terminal to official Codex, then reconnect when you exit it with `/quit`. Finish active turns and background terminals before handing off. On macOS/Linux, a POSIX PTY prefills the slash command without submitting it. On Windows, Codex inherits the console and you type the selected command manually. Official trust, login, permissions and platform restrictions still apply.

The session activity panel shows real subagent threads, backend-reported background processes and recent command output. It does not launch agents just to populate the UI. Existing personal Claude skills can be referenced from their original files; see [workflow compatibility](docs/claude-compatibility.md). Personal files are never included in this repository.

## Development and verification

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/live_check.py
.venv/bin/python scripts/clipboard_check.py  # macOS clipboard round trip; restores original contents
.venv/bin/python -m pip wheel --no-deps . --wheel-dir dist
```

Use `.venv\Scripts\python.exe` on Windows. Offline tests cover state transitions, slash commands, approvals, keyboard navigation, preferences, clipboard adapters and image payloads without model calls. CI is configured for macOS, Linux and Windows on Python 3.11 and 3.14. Local end-to-end verification has been performed on macOS; Windows/Linux desktop clipboard and native console behavior still require checks on those platforms. POSIX-only PTY tests are skipped on Windows.

The supplied Archaic-Atom black/white PNG logos are packaged with the app. The terminal header uses a sampled character version to avoid terminal-specific image protocols.

[Contributing](CONTRIBUTING.md) · [Codex App Server](https://learn.chatgpt.com/docs/app-server) · [Textual](https://textual.textualize.io/)
