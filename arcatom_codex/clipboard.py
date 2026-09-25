"""Desktop clipboard adapters with terminal fallback. 跨平台系统剪贴板。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageGrab


@dataclass
class ClipboardContent:
    """Clipboard text and saved image paths. 剪贴板文本与已保存图片路径。"""

    text: str = ""
    images: list[str] = field(default_factory=list)


def clipboard_command(write: bool) -> list[str] | None:
    """Use native utilities with fixed arguments, never a shell. 参数不经 shell。"""
    if sys.platform == "darwin":
        return ["pbcopy" if write else "pbpaste"]
    if sys.platform == "win32":
        script = (
            "$t=[Console]::In.ReadToEnd(); Set-Clipboard -Value $t"
            if write
            else "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Get-Clipboard -Raw"
        )
        prefix = "[Console]::InputEncoding=[Text.UTF8Encoding]::new(); "
        return [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-Command",
            prefix + script,
        ]
    candidates = (
        [
            (["wl-copy"], "wl-copy"),
            (["xclip", "-selection", "clipboard"], "xclip"),
            (["xsel", "--clipboard", "--input"], "xsel"),
        ]
        if write
        else [
            (["wl-paste", "--no-newline", "--type", "text"], "wl-paste"),
            (["xclip", "-selection", "clipboard", "-o"], "xclip"),
            (["xsel", "--clipboard", "--output"], "xsel"),
        ]
    )
    return next(
        (argv for argv, binary in candidates if shutil.which(binary)), None
    )


def copy_text(text: str) -> bool:
    """Return whether a desktop clipboard accepted the text. 无桌面时由终端接管。"""
    argv = clipboard_command(True)
    if not argv:
        return False
    try:
        subprocess.run(
            argv,
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
            check=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def save_image(image: Image.Image, cache: Path) -> str:
    """Keep attachments available to Codex and resumable history. 保存图片附件。"""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (uuid.uuid4().hex + ".png")
    image.save(path, format="PNG")
    return str(path.resolve())


def import_image(path: str, cache: Path) -> str:
    """Validate and snapshot an explicitly attached image. 检查并保存图片副本。"""
    with Image.open(Path(path).expanduser()) as image:
        image.load()
        return save_image(image, cache)


def read_clipboard(cache: Path) -> ClipboardContent:
    """Read only on an explicit paste action. 仅在用户粘贴时读取剪贴板。"""
    try:
        image = ImageGrab.grabclipboard()
    except (
        OSError,
        RuntimeError,
        NotImplementedError,
        subprocess.SubprocessError,
    ):
        image = None
    if isinstance(image, Image.Image):
        return ClipboardContent(images=[save_image(image, cache)])
    if isinstance(image, list):
        images = []
        for path in image:
            try:
                images.append(import_image(path, cache))
            except (OSError, ValueError):
                continue
        if images:
            return ClipboardContent(images=images)
    argv = clipboard_command(False)
    if argv:
        try:
            result = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=4,
                check=True,
            )
            return ClipboardContent(
                text=result.stdout.decode("utf-8").rstrip("\r\n")
                if sys.platform == "win32"
                else result.stdout.decode("utf-8")
            )
        except (OSError, UnicodeError, subprocess.SubprocessError):
            pass
    return ClipboardContent()
