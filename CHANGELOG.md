# Changelog

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
