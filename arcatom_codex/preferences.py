"""Local UI preferences, separate from backend settings. 独立于后端的界面偏好。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def approval_defaults(preferences: dict) -> dict[str, str]:
    """Route approvals through Codex review, preserving sandbox boundaries.

    默认启用官方自动审查；关闭后由用户确认，不在客户端自动批准。
    """
    return {
        "approvalPolicy": "on-request",
        "approvalsReviewer": "auto_review"
        if preferences.get("approve_for_me", True)
        else "user",
    }


def preference_path() -> Path:
    """Keep the legacy path so existing settings survive renames. 保留旧偏好路径。"""
    return Path.home() / ".config" / "arcatom" / "preferences.json"


def read_preferences(path: Path | None = None) -> dict:
    """Read a settings object or use defaults on invalid files.

    读取偏好，异常时使用默认值。
    """
    try:
        value = json.loads(
            (path or preference_path()).read_text(encoding="utf-8")
        )
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_preferences(value: dict, path: Path | None = None) -> None:
    """Replace preferences atomically after writing a temporary file.

    原子替换偏好文件。
    """
    target = path or preference_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".preferences-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
