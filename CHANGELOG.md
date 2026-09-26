# Changelog

## 0.2.0 — 2026-09-26

- Group session state, Codex transport, terminal adapters and application
  actions into separate packages, with compatibility aliases for old imports.
- Update compatible runtime and development dependencies. Python 3.11 keeps
  `textual-image` 0.12; Python 3.12+ uses 0.14.1.
- Upgrade Textual to 8.2.8 for native widget pointer styles, adapting text
  selection boundaries, select-form empty values and keyboard protocol flags.
  Keep the iTerm2 OSC 22 mapping for clickable transcript cells.
- Set iTerm2 pointer shapes by context: arrow over ordinary content, hand over
  links and clickable activity, text pointer during editing or selection, with
  the terminal pointer restored on exit.
- Show commands, searches, file changes and other activity immediately after
  reply text as collapsed, clickable `>` rows. Inline expansion shows only the
  request; results stay in the `Ctrl+T` detail view. Animate the arrow and
  running status marker, and remove extra blank rows before activity summaries.

- Align agent names, status, token usage and elapsed time across nested levels
  in the activity tree, recalculating after terminal resize while keeping the
  selected agent. Use readable rose Markdown links on dark and light themes,
  and remove duplicate blank rows around file-change and search summaries.
- Keep subagent names stable when collaboration events and thread polling report
  different labels. Open thread and command details at the newest content, with
  the latest command selected by default.
- Keep thread messages selectable for copying, show commands and tools as
  collapsed choices, and open one full output with Enter. Esc returns to the
  thread, then to the activity tree. Open clicked HTTP(S) Markdown links.
- Scope the activity tree and counts to the current or latest turn, so agents
  from earlier prompts do not accumulate beside newly spawned agents.
- Offer Codex-managed browser or device-code sign-in when the local account is
  missing, then refresh sessions automatically after login. Keep a home and
  `/login` entry point for users who defer it.
- Organize activity as a Main → subagent tree. Open it while subagents run and
  close it when they finish; each thread opens its own messages and activity.
- Refresh nested agent status from thread metadata and include each child
  thread's background processes and MCP tools in its details.

- Keep upward mouse selections inside the visible transcript when a drag reaches
  the title or other controls, so copied text matches the highlighted region.
- Show model reasoning effort beside the model in the chat header and status bar.
- Surface live subagents, nested agents, dynamic tools and commands in the
  activity panel; open it automatically during active work and allow manual
  toggling with `Ctrl+T`.

- Present MCP extension forms with validated text, number, boolean, single-choice
  and multi-choice fields, plus copyable URL requests; return the user's submit,
  decline or cancel decision.

- Accept the numeric keypad decimal key under the enhanced keyboard protocol
  in chat and search inputs, while preserving shortcuts and Delete.

- Put chat connection status beside AtomX / CODEX and the directory, model and
  session status beside the session title, retaining a blank separator below.
- Anchor streamed output across all layout passes, including waiting-bar,
  composer and viewport height changes. Sending a new message resumes following;
  scrolling up or selecting text pauses it.
- Keep transcript width unchanged when it gains focus, preventing wrapped text
  and mouse selection from shifting during a drag.

- Keep a theme-colored `>` prompt visible in the chat composer without adding
  it to drafts, copied text or submitted messages.

- Place the home connection status beside the robot, below the AtomX title;
  retain its former standalone row as blank space.

- Limit local shared-connection close handshakes to one second so quitting
  does not wait ten seconds for an unresponsive peer; keep the server running.

- Show execution elapsed time in days, hours, minutes and seconds instead of
  an ever-growing seconds count.

- Ignore delayed session selection events after the interface closes, avoiding
  an intermittent shutdown exception.
- Stop timer and queued view refreshes as shutdown begins, before child widgets
  are removed, preventing partial-unmount status bar errors.

- Show AtomX and connected Codex versions in the home header; read the backend
  version from its handshake and report the current AtomX client version.

## 0.1.1 — 2026-09-25

- Add F5 reply copying: choose a reply, then original Markdown, unwrapped plain
  text, or an individual code block with its source indentation.
- Add F7 / click image gallery with previous/next, zoom, fit, open original and
  Escape back to the conversation without losing the draft or stopping work.
- Add a team-maintained Homebrew tap for installation and upgrades.

- Copy selected answers without terminal line-fill spaces or decorative code
  margins, preserving source indentation, paragraph breaks and table alignment.

- Display conversation images inline, including local attachments, Markdown image
  references, generated/viewed images and structured tool results. Decode and
  cache previews asynchronously, preserve selectable surrounding text, and use
  native terminal graphics with a colored half-cell fallback.

## 0.1.0 — 2026-09-25

First public release of AtomX.

- Rename the terminal workspace to **AtomX** with an `atomx` command; remove the old
  `arcatom` command while preserving the Python namespace, preferences, attachments and shared
  backend paths for compatibility.
- Replace the team mark in the home header with a robot drawn using equal-width
  ASCII borders. Keep the compact layout and single-line conversation header.
- Default to the terminal's existing keyboard protocol. Make enhanced keyboard
  reporting an explicit restart-only setting or command-line override; retain
  Ctrl+J as the portable newline shortcut. Reserve desktop navigation chords.
- Recognize double-arrow jumps from input timestamps and apply scroll jumps
  immediately, preserving the draft and the return-to-composer sequence.
- Separate widgets, dialogs, rendering, navigation, history, backend handling and
  turn mutations from the main application. Add typed signatures and bilingual
  docstrings; enforce formatting, lint and type checks in CI.
- Fix the incomplete requirements entry point and verify the AtomX launcher and the
  installable wheel. Keep MIT licensing and `master` as the default branch.

- Animate terminal tab titles with session status and restore the prior title
  when exiting or handing off to native Codex. Move all toast notifications to
  the upper right on every screen.

Headless tests cover application behavior. Desktop shortcut delivery, clipboard
access and Shift+Enter behavior remain dependent on the platform and terminal.
