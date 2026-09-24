"""Launch Codex without a shell on every platform. 跨平台启动，参数不经 shell。"""
from .i18n import tr
import os
from pathlib import Path
import shutil


def executable_argv(binary: str, *, windows: bool | None = None) -> list[str]:
    """Resolve npm's Windows shim to its Node entrypoint. 解析 npm Windows 启动器。"""
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return [binary]
    path = Path(shutil.which(binary) or binary)
    if path.suffix.lower() not in (".cmd", ".bat", ".ps1"):
        return [str(path)]
    candidates = [path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
                  path.parent.parent / "@openai" / "codex" / "bin" / "codex.js"]
    entry = next((p for p in candidates if p.is_file()), None)
    node = shutil.which("node")
    if entry and node:
        return [node, str(entry)]
    raise RuntimeError(tr('无法识别 Codex 启动脚本。请安装官方 npm 包，或用 --codex 指定 codex.exe。'))
