"""Slash command catalog, pinned to the installed Codex CLI 0.156.1.

菜单和分发共用一个索引；原生专用命令明确标注，避免空实现。
"""
from dataclasses import dataclass
from .i18n import tr


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    native: bool = False
    argument: str = ""


COMMANDS = [
    Command("settings", "设置界面主题、目录与使用习惯"),
    Command("palette", "界面调色板与强调色"),
    Command("model", "选择模型和推理强度"),
    Command("reasoning", "调整当前模型的推理强度"),
    Command("permissions", "选择会话权限"),
    Command("plan", "切换计划模式", argument="可选任务"),
    Command("fast", "切换模型提供的快速服务档位"),
    Command("review", "审查工作区、分支或指定内容", argument="可选审查要求"),
    Command("new", "新建会话", argument="可选任务"),
    Command("resume", "返回历史会话", argument="可选会话 ID"),
    Command("fork", "复制当前会话继续工作", argument="可选任务"),
    Command("compact", "压缩当前会话上下文"),
    Command("recap", "总结当前会话"),
    Command("rename", "修改会话名称", argument="新名称"),
    Command("skills", "选择 Codex 和个人 Claude 技能"),
    Command("agents", "查看全部会话和代理"),
    Command("subagents", "进入当前会话的子代理"),
    Command("agent", "进入当前会话的子代理"),
    Command("goal", "设置、查看或调整持续目标", argument="目标 / pause / resume / clear"),
    Command("side", "创建临时分支对话", argument="可选问题"),
    Command("btw", "创建临时分支对话", argument="可选问题"),
    Command("status", "查看模型、权限、用量和会话信息"),
    Command("usage", "查看账户和会话用量", argument="可选 reset"),
    Command("diff", "查看 Git 修改，包含未跟踪文件"),
    Command("copy", "复制最近的完整回复"),
    Command("export", "导出对话为 Markdown", argument="可选文件路径"),
    Command("mention", "选择文件并插入路径", argument="可选文件路径"),
    Command("cd", "修改会话工作目录", argument="目录"),
    Command("pwd", "显示会话工作目录"),
    Command("cwd", "显示会话工作目录"),
    Command("ps", "查看后台进程和输出"),
    Command("stop", "停止全部后台终端"),
    Command("clean", "停止全部后台终端"),
    Command("init", "让 Codex 为当前项目生成 AGENTS.md"),
    Command("mcp", "查看 MCP 服务和工具", argument="可选 verbose"),
    Command("debug-config", "查看有效配置与配置来源"),
    Command("warnings", "查看连接和后端诊断信息"),
    Command("rollout", "显示当前会话的存储路径"),
    Command("raw", "切换纯文本对话视图"),
    Command("theme", "选择代码语法高亮主题"),
    Command("statusline", "选择底部状态栏字段和顺序"),
    Command("title", "选择终端标题内容"),
    Command("clear", "清空显示并开始新会话", argument="可选任务"),
    Command("archive", "归档当前会话并返回列表"),
    Command("delete", "永久删除当前会话及其子会话"),
    Command("help", "查看全部命令和键盘操作"),
    Command("quit", "退出 Arcatom"),
    Command("exit", "退出 Arcatom"),
    # These workflows belong to the official TUI, not the app-server API.
    Command("ide", "选择 IDE 上下文", True, "可选提示"),
    Command("keymap", "配置原生 Codex 快捷键", True),
    Command("vim", "切换原生编辑器 Vim 模式", True),
    Command("experimental", "管理实验功能", True),
    Command("approve", "批准重试被自动审核拒绝的操作", True),
    Command("memories", "管理记忆使用与生成", True),
    Command("import", "导入 Claude / Cursor 配置与会话", True),
    Command("hooks", "管理生命周期 hooks", True),
    Command("worktree", "在 Git worktree 中继续任务", True),
    Command("app", "在官方桌面应用中继续", True),
    Command("voice", "语音与声音设置", True, "可选 settings"),
    Command("tui", "选择原生终端界面模式", True),
    Command("daemon", "管理官方后台服务", True),
    Command("pets", "选择原生终端宠物", True),
    Command("pet", "选择原生终端宠物", True),
    Command("apps", "浏览和管理应用连接器", True),
    Command("plugins", "浏览、安装和管理插件", True),
    Command("logout", "退出 Codex 账户登录", True),
    Command("feedback", "提交反馈并选择是否附带日志", True),
    Command("setup-default-sandbox", "配置 Windows 沙箱（Windows 专用）", True),
    Command("sandbox-add-read-dir", "增加 Windows 沙箱可读目录", True, "目录"),
    Command("test-approval", "原生审批调试工具", True),
    Command("debug-m-drop", "原生记忆调试工具（会删除记忆）", True),
    Command("debug-m-update", "原生记忆调试工具", True),
]
BY_NAME = {command.name: command for command in COMMANDS}


def matches(text: str) -> list[Command]:
    """Match only a single leading slash token. 只在命令名位置弹出提示。"""
    if not text.startswith("/") or any(c.isspace() for c in text):
        return []
    query = text[1:].casefold()
    return sorted((c for c in COMMANDS if query in c.name.casefold() or query in c.description or query in tr(c.description).casefold()),
                  key=lambda c: (c.name != query, not c.name.startswith(query), COMMANDS.index(c)))
