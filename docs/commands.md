# 命令覆盖与行为

核对版本：本机 `codex-cli 0.156.1`。以该版本官方 `tui/src/slash_command.rs` 的命令枚举和别名为基准，并参考 [官方命令文档](https://learn.chatgpt.com/docs/developer-commands?surface=cli)。菜单共 72 项（包含别名与 Arcatom 补充入口），不是 72 个独立后端能力。

## 应用内处理（48 项）

| 命令 | 行为 |
| --- | --- |
| settings / palette | 语言、整套界面配色、强调色、自动跟随、紧凑布局与默认目录；F2 也可打开 |
| model / reasoning | 从 model/list 分页读取模型与强度，通过 thread/settings/update 更新当前会话；取消和失败都不伪报成功 |
| permissions | 从后端读取允许选择的权限配置，应用到当前会话 |
| plan | 切换计划 / 执行模式；可附带任务；模型切换保留当前模式 |
| fast | 切换当前模型目录提供的 Fast 档位，没有对应档位时说明原因 |
| review | 工作区、分支、提交或自定义审查，使用 review/start |
| new / clear | 新建会话，可在命令后附带任务 |
| resume / agents | 回到会话列表；resume 可直接带会话 ID |
| fork / side / btw | 分叉当前会话；side / btw 使用临时分叉，可在列表切回主会话 |
| compact / recap | 后端压缩上下文 / 向模型请求当前会话总结 |
| rename | 修改名称；没有参数时弹出输入框 |
| skills | 合并后端技能与用户的 Claude 技能，选择后插入技能名称 |
| subagents / agent | 选择并进入当前会话的子代理 |
| goal | 读取目标，设置目标文字，或 pause / resume / clear |
| status / usage | 当前配置和用量；usage 带参数时进入原生账户用量流程 |
| diff | 查看 Git 暂存、未暂存和未跟踪文件差异；超大结果截断 |
| copy | 通过系统剪贴板和终端 OSC 52 复制最近的完整回复 |
| export | 导出已加载会话为 Markdown；文件已存在时拒绝覆盖 |
| mention | 选择文件或输入路径，插入待发送文本；扫描最多 1500 项 |
| cd / pwd / cwd | 修改或查看会话工作目录 |
| ps / stop / clean | 查看后台终端 / 停止全部后台终端；Ctrl+C 才是停止模型回合 |
| init | 让 Codex 检查项目并生成或完善 AGENTS.md |
| mcp | 读取 MCP 服务和工具清单 |
| debug-config / warnings / rollout | 配置来源、诊断信息和会话存储路径；配置认证字段遮盖 |
| raw | 切换纯文本消息显示 |
| theme / statusline / title | 设置代码高亮、底部字段顺序和终端标题，保存 Arcatom 显示偏好 |
| archive / delete | 归档或永久删除当前会话，返回 Arcatom 列表 |
| help / quit / exit | 查看帮助或退出 Arcatom |

原生 /archive、/delete 完成后退出官方 CLI；Arcatom 中完成后返回列表。这是会话工作台的有意差异。

## 官方界面处理（24 项）

`/ide`、`/keymap`、`/vim`、`/experimental`、`/approve`、`/memories`、`/import`、`/hooks`、`/worktree`、`/app`、`/voice`、`/tui`、`/daemon`、`/pets`、`/pet`、`/apps`、`/plugins`、`/logout`、`/feedback`、`/setup-default-sandbox`、`/sandbox-add-read-dir`、`/test-approval`、`/debug-m-drop`、`/debug-m-update`。

这些菜单项标为 `[原生]`。Arcatom 暂时让出终端，由官方 Codex 恢复同一会话并显示原生交互。macOS / Linux 检测到正常输入框后只填入命令，不自动按 Enter；你可检查后执行。Windows 直接接管控制台，请手动输入所选命令。输入 /quit 后回到 Arcatom，并重连后端加载变更。

如果出现登录、目录信任等前置页面，先按官方流程完成，再输入所选命令。Arcatom 不代为批准这些步骤。正在执行的回合和后台终端需先结束，以免重连时丢失运行状态。临时分叉不能交给另一个进程恢复，应先使用普通 /fork。

原生编辑设置（如 /vim、/keymap、/tui）作用于官方界面，不会把 Textual 编辑器变成官方编辑器；宠物也显示在官方界面。Windows 专用命令、调试命令以及受实验开关限制的功能仍以官方可用性为准。Arcatom 没有模拟这些能力。

## 验证范围

- 自动化覆盖命令枚举、提示筛选、补全、取消、首页入口、模型和推理强度选择、失败回滚、会话隔离、计划模式保留、压缩和停止的接口路由。
- 真实 app-server 测试读取了 7 个可选模型，确认配置切换后的模型及强度，验证计划模式设置与权限、MCP 列表接口。
- PTY 测试验证命令只预填、不自动提交，以及退出和终端恢复。真实官方界面验证保留目录信任步骤；不在测试中批准用户目录信任，也不提交反馈、退出真实账户或执行插件安装。
- 演示截图位于 `artifacts/commands.png` 和 `artifacts/models.png`，不含个人会话数据。
