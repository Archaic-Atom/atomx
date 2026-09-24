# Arcatom Codex

一个按 Claude Code 式终端习惯设计的 Codex 客户端：暖色界面、键盘导航、会话搜索、用量明细，以及会话内部的子代理与进程面板。使用本机 Codex；不是 Claude 客户端，也不需要安装 Claude。

## 启动

需要 Python 3.11+ 和已经登录的 Codex CLI。当前验证版本为 `codex-cli 0.156.1`。开发与测试主要在 macOS 上完成；原生终端接入依赖 POSIX PTY。

在项目根目录安装并启动：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
codex login
./arcatom
```

也可安装用户级启动命令，之后从任意项目目录运行 `arcatom`：

```bash
python3 scripts/install_launcher.py
arcatom
```

默认使用启动时所在目录，新建会话时可修改。历史会话保留自己的工作目录。

```bash
arcatom --cwd /path/to/project
arcatom --demo   # 离线示例数据，不调用模型
arcatom --check  # 验证本机连接，不发送模型请求
```

也可安装为 Python 包，使用虚拟环境中的命令：

```bash
.venv/bin/pip install .
.venv/bin/arcatom --demo
```

## 操作

在聊天输入框或首页搜索框输入 `/`，立即出现命令提示。继续输入可筛选，`↑↓` 选择，`Tab` 补全命令，`Enter` 执行，`Esc` 收起。

`/model` 打开后端实时模型列表，选择模型后再选择推理强度；确认成功才更新状态栏，从下一回合生效。仅修改当前会话，保留其他会话的模型。`/reasoning` 可单独调整强度。

已按本机 Codex CLI **0.156.1** 接入其内置命令及别名，加上 Arcatom 入口共 **70 项**：46 项在应用内处理，24 项标为 **[原生]**。详细对应关系见 [命令覆盖说明](docs/commands.md)。

带 `[原生]` 的命令在同一终端打开官方 Codex 界面，准备好命令后由你按 Enter 执行，使用 `/quit` 返回 Arcatom。首次进入目录时仍会出现官方登录或信任流程；完成后若命令尚未填入，输入所选命令即可。返回后重新连接后端以加载配置变化。进入前需完成或停止运行中的回合和后台终端。

| 操作 | 按键 / 命令 |
| --- | --- |
| 输入完全为空时返回会话列表 | `←` |
| 输入有文字时移动光标 | `←` / `→`，保持正常编辑 |
| 列表搜索 | 直接输入会话名称或目录 |
| 选择 / 打开会话 | `↑` / `↓`，`Enter` |
| 永久删除选中会话及其子会话 | 列表中 `Ctrl+X`，直接删除，无二次弹窗 |
| 新建会话 | `Ctrl+N`、`/new` |
| 发送消息 | `Enter` |
| 命令提示 / 补全 | `/`、`↑↓`、`Tab`、`Enter`、`Esc` |
| 切换模型 / 推理强度 | `/model`、`/reasoning` |
| 权限 / 计划 / 审查 / 压缩 | `/permissions`、`/plan`、`/review`、`/compact` |
| 换行 | `Ctrl+J`、支持扩展按键的终端可用 `Shift+Enter` |
| 代理与进程面板 | `Ctrl+T`、`/agents`、`/ps` |
| 查看代理输出 / 命令日志 | 面板内 `↑` / `↓`，`Enter` |
| 用量 | `Ctrl+U`、`/usage` |
| 刷新列表 / 重新连接 | `Ctrl+R` |
| 停止当前回合 | `Ctrl+C` |
| 停止全部后台终端 | `/stop`、`/clean`（与 Codex 原生命令语义一致） |
| 返回、关闭详情 | `Esc` |
| 重命名会话 | `/rename 新名字` |
| 选择 Codex / 个人技能 | `/skills`；`$技能名` 显式使用 |
| 退出 | `Ctrl+Q`、`/quit` |
| 帮助 | `/help` |

在会话运行中发送消息会引导当前回合（`turn/steer`）。返回列表不会终止任务，切换会话会保留草稿。退出应用会关闭该应用启动的 Codex 服务；仍有活动回合时会提示确认。并非后台常驻服务。

`Ctrl+X` 调用 Codex 的永久删除接口，删除选中历史及其子会话；它也会从其他 Codex 客户端的历史中消失。已知正在运行的主会话需先停止；删除失败会保留条目。聊天输入框中的 `Ctrl+X` 仍用于剪切。

按 Enter 后立即回显消息并显示等待动画、阶段和经过时间，覆盖恢复历史、等待模型、调用工具和流式回复。完成或断线后停止动画。发送失败保留草稿，不自动重复发送。动画是本客户端按 Codex 事件绘制的等待指示。

支持中文、粘贴、多行输入、Markdown 与代码显示。推荐终端尺寸至少 80×24；按 `Tab` 移动焦点，滚轮或聚焦记录区后 `PageUp` / `PageDown` 查看历史。遵守 `NO_COLOR` 环境变量；若你的环境设置了它但希望显示暖色，可用 `env -u NO_COLOR arcatom`。

## 会话内的子代理与进程

`Ctrl+T` 展开当前会话的面板。子代理通过 Codex 的真实协作事件和子线程列表发现，不会仅为填充界面而启动。需要时在消息中明确让 Codex 分工。

- 子代理：显示名称、运行状态和可读到的 Token 计数。回车打开输出详情，每两秒刷新。
- 后台进程：显示 Codex 报告的命令、进程 ID。回车查看收到的输出流。
- 普通命令：保留状态、日志和退出码，面板显示最近 30 条。

后端每四秒刷新当前会话的代理与后台进程。普通消息和命令输出通过事件流实时更新。外部 Codex 客户端正在执行的任务可能属于另一个服务进程，不能保证同步其运行状态或控制其进程；历史记录仍可读取。不要在两个客户端同时向同一会话发送任务。

## 用量数据

用量常驻底部，按以下顺序显示：

```text
14:30:22 │ tok:28K │ ctx:94% │ 5h:76% 2h 10m │ 7d:63% 3d 8h │ 模型
```

聊天时显示当前会话，列表中显示选中的会话。`tok` 为累计 Token；`ctx` 为最近一轮上下文的剩余比例；额度窗口也显示**剩余**百分比，并在后端提供时间时显示重置倒计时。剩余 ≥50% 为绿，20–49% 为黄，低于 20% 为红。缺失的上下文、额度或模型字段直接隐藏，未知 Token 显示 `—`。窄终端按字段换行。

会话计数跟随事件更新，账户数据每分钟刷新一次，`Ctrl+R` 可手动刷新。输入、输出、缓存及账户累计明细可按 `Ctrl+U` 查看。

`/statusline` 可调整字段与顺序，`/title` 配置终端标题，`/theme` 选择代码语法高亮主题。这些显示偏好保存到 `~/.config/arcatom/preferences.json`，演示模式不会读写此文件。

- 账户累计、最近每日用量和额度窗口来自 `account/usage/read`、`account/rateLimits/read`；是否可用取决于登录方式和后端。
- 当前会话计数来自 `thread/tokenUsage/updated`。
- 本地历史计数从 Codex 返回的 JSONL 文件中读取最后一个累计快照，不把每次快照相加。
- 缓存是输入的一部分；继承/分叉历史也可能导致不同会话的累计计数重叠。因此不把本地会话计数冒充账户账单，不推算费用。
- `—` 表示数据尚不可用，而不是零。历史列表自动分页，默认显示未归档的普通会话；子代理显示在所属会话内部。

历史用量读取限制在 Codex 数据目录内 JSONL 文件的最后 4 MiB。极长的最后一条输出可能让旧快照不在范围内，此时显示 `—`。聊天视图显示最近 400 个记录项；完整记录由 Codex 保存。

## 权限与兼容性

自动发现 `~/.claude/skills` 中的个人技能，跟随已有软链引用原始 `SKILL.md`，并引用 `~/.claude/CLAUDE.md` 的工作偏好。`/skills` 会合并后端返回的 Codex 技能与个人技能，选择后插入 `$技能名`，再输入任务。索引作为每次消息的应用上下文传给 Codex，不覆盖现有开发者指令或全局配置。显式调用时直接附上原文件的最新正文；其他相关技能正文及参考资料由模型按需读取。

技能接入方式与迁移边界见 [Claude 习惯与技能接入](docs/claude-compatibility.md)。个人技能文件不随本仓库分发。Claude 专用工具和插件不会因技能引用而自动获得；所需工具不可用时仍需相应适配。`synced` 工具包不纳入个人技能目录。

通过 `codex app-server --stdio` 连接，沿用本机登录及 Codex 配置。不会读取或复制认证文件，不修改全局 Codex 配置，不绕过其沙箱与审批。

命令/文件修改审批、额外权限请求和结构化问题会显示交互弹窗。审批默认焦点为拒绝；不自动批准、不保存永久放行规则。当前尚未实现的 MCP 扩展表单等交互会明确拒绝。命令目录覆盖本机版本，专用功能交给官方界面处理；账户权限、平台限制及实验开关仍由 Codex 决定，例如 Windows 沙箱命令无法在 macOS 执行。

## 验证与开发

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/live_check.py
.venv/bin/python scripts/live_check.py --commands  # 临时会话内验证模型 / 设置 API，不调用模型
.venv/bin/python scripts/live_check.py --native    # 原生界面或信任提示及返回；不批准信任
# 可选：真实模型最小临时会话测试，会产生少量用量
.venv/bin/python scripts/live_check.py --prompt
# 可选：验证原目录技能引用、应用上下文和消息关联 ID
.venv/bin/python scripts/live_check.py --skills
```

`--demo` 使用明确标注的示例数据，和真实模式隔离。生成的本地界面预览可放在 `artifacts/`；该目录不纳入 Git。

结构：`rpc.py` 管理 JSONL 连接；`state.py` 处理事件与用量；`ui.py` 与 `style.tcss` 实现界面；`demo.py` 为离线演示与测试提供同一套数据。

参考：[官方 Codex App Server](https://learn.chatgpt.com/docs/app-server)、[官方子代理说明](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[Textual](https://textual.textualize.io/)。
