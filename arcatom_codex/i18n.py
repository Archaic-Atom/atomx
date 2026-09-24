"""Translate UI-owned strings; user and model content stays untouched.

仅翻译应用界面；用户文本、模型输出、文件路径与后端标识保持原样。
"""
import json
import os
from pathlib import Path

from textual._context import active_app

ENGLISH = json.loads((Path(__file__).parent / "locales" / "en.json").read_text(encoding="utf-8"))


def tr(source: str, language: str | None = None) -> str:
    """Resolve language at render time, defaulting to English. 默认英文。"""
    if language is None:
        app = active_app.get(None)
        language = getattr(app, "language", os.environ.get("ARCATOM_LANGUAGE", "en"))
    return source if language == "zh" else ENGLISH.get(source, source)
