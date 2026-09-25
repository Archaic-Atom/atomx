"""Install a user-local launcher without modifying shell configuration or Codex."""
from pathlib import Path
import shlex
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
if os.name == "nt":
    # pip creates the platform-native console launcher in the Python Scripts dir.
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", str(root)], check=True)
    print("Installed atomx.exe in this Python environment's Scripts directory.")
    raise SystemExit(0)
target = Path.home() / ".local" / "bin" / "atomx"
content = "#!/bin/sh\n# AtomX launcher\nexec " + shlex.quote(str(root / "atomx")) + ' "$@"\n'
if target.exists() or target.is_symlink():
    if target.is_symlink() or target.read_text() != content:
        raise SystemExit(f"已有不同的启动文件，未覆盖：{target}")
else:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
target.chmod(0o755)
print(f"Installed: {target}")
