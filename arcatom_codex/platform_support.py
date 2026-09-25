"""Launch Codex without a shell on every platform. 跨平台启动，参数不经 shell。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .i18n import tr


def open_image_file(path: Path) -> None:
    """Open an explicit local image using the system viewer, without a shell.

    用户主动打开本地图片时调用系统查看器，参数不经 shell。
    """
    if sys.platform == "win32":
        getattr(os, "startfile")(str(path))
    else:
        command = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run(
            [command, str(path)], check=True, capture_output=True, timeout=15
        )


def executable_argv(binary: str, *, windows: bool | None = None) -> list[str]:
    """Resolve npm's Windows shim to its Node entrypoint. 解析 npm Windows 启动器。"""
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return [binary]
    path = Path(shutil.which(binary) or binary)
    if path.suffix.lower() not in (".cmd", ".bat", ".ps1"):
        return [str(path)]
    candidates = [
        path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
        path.parent.parent / "@openai" / "codex" / "bin" / "codex.js",
    ]
    entry = next((p for p in candidates if p.is_file()), None)
    node = shutil.which("node")
    if entry and node:
        return [node, str(entry)]
    raise RuntimeError(
        tr(
            "无法识别 Codex 启动脚本。请安装官方 npm 包，或用 --codex 指定 codex.exe。"
        )
    )
