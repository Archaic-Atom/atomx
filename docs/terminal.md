# Terminal keyboard setup / 终端键盘设置

Fonts and font size are controlled by the terminal. AtomX does not change them.
字体和字号由终端控制，AtomX 不修改这些设置。

Select text, then press Ctrl+C or F3 to copy. Selecting alone never writes the clipboard.
选中文字后按 Ctrl+C 或 F3 复制；单纯选择不会写入剪贴板。

## Cmd+C in iTerm2

iTerm2 normally handles Cmd+C itself, so an application-drawn selection is not its native selection. AtomX also accepts a forwarded `super+c` (Cmd+C) key.
iTerm2 默认自行处理 Cmd+C，无法识别应用内部的文字选区。AtomX 已支持转发进来的 Cmd+C。

In iTerm2 **Settings → Profiles → your profile → Keys → Key Mappings**, add:
在 iTerm2 **设置 → Profiles → 当前配置 → Keys → Key Mappings** 新增：

- Keyboard Shortcut: **Cmd+C**
- Action: **Send Escape Sequence**
- Value: **`[99;9u`** (do not include a literal Escape character / 不要额外输入 Esc 字符)

This changes Cmd+C for that terminal profile. Use a dedicated profile for AtomX if you want other terminal sessions to retain native Cmd+C. Remove the mapping to restore native Copy. AtomX does not silently alter terminal profiles.
此映射对该终端配置生效；如需其他终端会话继续使用原生 Cmd+C，请为 AtomX 使用独立配置。删除映射即可恢复原生复制。AtomX 不会自动改写终端配置。

Reference / 参考：[iTerm2 key mappings](https://iterm2.com/documentation-preferences-profiles-keys.html).

## Desktop navigation / 系统桌面切换

AtomX defaults to standard keyboard mode and filters Textual's paired Kitty
keyboard push/pop writes, including suspend/resume. It does not enable global
keyboard grabs. Enhanced mode is available through Settings or
`--keyboard-mode enhanced`; `--keyboard-mode standard` overrides saved preferences.
默认使用普通键盘模式，启动、暂停和恢复时不改写 Kitty 键盘协议栈；增强模式须主动开启。

Ctrl+J is a portable newline. Shift+Enter needs distinct terminal encoding; in
standard mode a terminal may send it as ordinary Enter. Do not assume it can be
distinguished without terminal support.
Ctrl+J 始终换行；普通模式下 Shift+Enter 是否可区分取决于终端。

Test actual OS navigation from home, editing, transcript browsing and settings,
then after a native Codex handoff and after restarting. On macOS test all four
Control+arrows; on Windows test Win+Ctrl+Left/Right; on Linux test the desktop's
configured workspace shortcuts. AtomX neither changes those settings nor can
replay keys that the terminal consumes before delivering input.
分别在首页、编辑、浏览、设置、原生界面返回及重启后测试系统切换。程序不修改系统设置，
也无法重放终端已截获的系统快捷键。

Protocol reference / 协议参考：
[Kitty keyboard protocol](https://sw.kovidgoyal.net/kitty/keyboard-protocol/).

## Live tab title / 动态标签标题

The tab title follows the current session, animates during work, and distinguishes
waiting, completed, stopped, failed and disconnected states. `/title` controls
whether the label contains the session and model. Home shows the overall queue.
The original terminal title is restored on exit and native Codex handoff.
标题随会话和状态更新；工作中有动画，首页显示整体队列。`/title` 选择标题内容。
退出或进入原生界面时恢复原标题，恢复应用后重新显示实时标题。

AtomX uses OSC 0 and the paired title-save/title-restore operations documented in
[XTerm control sequences](https://invisible-island.net/xterm/ctlseqs/ctlseqs.pdf).
Terminal profiles that prohibit application title updates retain their own label.
使用终端标题协议并成对保存和恢复；禁止应用改标题的终端配置仍使用自己的标题。

All toast messages, including copy, completion, warnings and errors, stack in the
upper right on both the workspace and modal screens. Actual approval choices
remain keyboard-accessible dialogs.
复制、完成、警告和错误等全部浮动消息在右上角堆叠；审批选项仍可用键盘操作。
