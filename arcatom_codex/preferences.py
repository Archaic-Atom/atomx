"""Local UI preferences, separate from Codex's permission and model config."""
import json
import os
from pathlib import Path
import tempfile


def preference_path() -> Path:
    return Path.home() / ".config" / "arcatom" / "preferences.json"


def read_preferences(path: Path | None = None) -> dict:
    try:
        value = json.loads((path or preference_path()).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_preferences(value: dict, path: Path | None = None) -> None:
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
