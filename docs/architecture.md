# AtomX code structure / 代码结构

The package is divided by responsibility. New code should use the canonical paths
below; the short modules at the package root only preserve older import paths.

项目按职责划分。新代码使用下列正式路径；包根目录中的短模块只为兼容旧导入路径。

| Package | Responsibility / 职责 | May depend on / 可依赖 |
| --- | --- | --- |
| `core/` | Session state, event reduction, command definitions / 会话状态、事件归并、命令定义 | Standard library and translation helpers |
| `backend/` | Codex RPC transport, shared local server, offline fixture / 协议传输、共享服务、离线演示 | `core/` and platform helpers; never Textual widgets |
| `terminal/` | Keyboard protocol and iTerm2 pointer adaptation / 键盘协议、鼠标指针适配 | Textual driver and standard library; never session state |
| `actions/` | Application workflows grouped as mixins / 按工作流划分的应用行为 | `core/`, `backend/`, and presentation components |
| Package root | Application composition, widgets, rendering, dialogs, settings, images, and entry point / 应用组装、控件与入口 | The packages above |

`ui.py` owns the `AtomXApp` composition and Textual lifecycle. Its action mixins
live in `actions/`; protocol code must not import `ui.py`. The `DemoClient` follows
the same call surface as `CodexClient`, which keeps UI tests offline.

`ui.py` 负责 `AtomXApp` 组装和 Textual 生命周期。行为模块放在 `actions/`；协议层
不得反向导入 `ui.py`。`DemoClient` 与 `CodexClient` 保持相同调用接口，让界面测试
可以离线运行。

The former import paths, such as `arcatom_codex.state` and
`arcatom_codex.rpc`, remain module aliases. They refer to the same module objects as
`arcatom_codex.core.state` and `arcatom_codex.backend.rpc`, so existing imports and
test patches continue to work. Do not add implementation to these aliases.

旧导入路径仍是模块别名，指向正式路径的同一个模块对象，以兼容已有调用和测试补丁。
不要在这些别名文件中加入实现。
