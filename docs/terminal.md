# Terminal keyboard setup / 终端键盘设置

Fonts and font size are controlled by the terminal. Arcatom does not change them.
字体和字号由终端控制，Arcatom 不修改这些设置。

Select text, then press Ctrl+C or F3 to copy. Selecting alone never writes the clipboard.
选中文字后按 Ctrl+C 或 F3 复制；单纯选择不会写入剪贴板。

## Cmd+C in iTerm2

iTerm2 normally handles Cmd+C itself, so an application-drawn selection is not its native selection. Arcatom also accepts a forwarded `super+c` (Cmd+C) key.
iTerm2 默认自行处理 Cmd+C，无法识别应用内部的文字选区。Arcatom 已支持转发进来的 Cmd+C。

In iTerm2 **Settings → Profiles → your profile → Keys → Key Mappings**, add:
在 iTerm2 **设置 → Profiles → 当前配置 → Keys → Key Mappings** 新增：

- Keyboard Shortcut: **Cmd+C**
- Action: **Send Escape Sequence**
- Value: **`[99;9u`** (do not include a literal Escape character / 不要额外输入 Esc 字符)

This changes Cmd+C for that terminal profile. Use a dedicated profile for Arcatom if you want other terminal sessions to retain native Cmd+C. Remove the mapping to restore native Copy. Arcatom does not silently alter terminal profiles.
此映射对该终端配置生效；如需其他终端会话继续使用原生 Cmd+C，请为 Arcatom 使用独立配置。删除映射即可恢复原生复制。Arcatom 不会自动改写终端配置。

Reference / 参考：[iTerm2 key mappings](https://iterm2.com/documentation-preferences-profiles-keys.html).
