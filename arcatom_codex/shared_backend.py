"""Connect local windows to one official server. 多窗口共享官方本地服务。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from .platform_support import executable_argv


def server_socket(binary: str) -> Path:
    """Return a short, per-user socket path. 按用户及 Codex 配置隔离。"""
    import shutil
    identity = str(Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve())
    identity += "|" + str(Path(shutil.which(binary) or binary).resolve())
    digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
    directory = Path(tempfile.gettempdir()) / f"arcatom-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise RuntimeError("Arcatom runtime directory has an unexpected owner.")
    directory.chmod(0o700)
    return directory / f"codex-{digest}.sock"


def ensure_local_server(binary: str, cwd: str | None) -> str:
    """Start once under a lock; leave it alive after a window closes.

    文件锁保护并发启动，退出单个窗口不会终止其他窗口的任务。
    """
    import fcntl
    endpoint = server_socket(binary)

    def ready() -> bool:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(.2)
            try:
                connection.connect(str(endpoint))
                return True
            except OSError:
                return False

    with endpoint.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if ready():
            return str(endpoint)
        if endpoint.exists():
            if not endpoint.is_socket():
                raise RuntimeError("Unexpected file at the Arcatom socket path.")
            endpoint.unlink()
        log_path = endpoint.with_suffix(".log")
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [*executable_argv(binary), "app-server", "--listen", f"unix://{endpoint}"],
                cwd=cwd, env=dict(os.environ, NO_COLOR="1"),
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if ready():
                return str(endpoint)
            if process.poll() is not None:
                raise RuntimeError(log_path.read_text(errors="replace")[-1600:])
            time.sleep(.05)
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError("Timed out starting the shared local Codex server.")
