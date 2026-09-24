"""Install a user-local launcher without modifying shell configuration or Codex."""
from pathlib import Path
import shlex

root = Path(__file__).resolve().parents[1]
target = Path.home() / ".local" / "bin" / "arcatom"
content = "#!/bin/sh\n# Arcatom Codex launcher\nexec " + shlex.quote(str(root / "arcatom")) + ' "$@"\n'
if target.exists() or target.is_symlink():
    if target.is_symlink() or target.read_text() != content:
        raise SystemExit(f"已有不同的启动文件，未覆盖：{target}")
else:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
target.chmod(0o755)
print(f"Installed: {target}")
