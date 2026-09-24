"""Exercise native handoff on a newly created disposable test thread only."""
import os
import re
import select
import struct
import subprocess
import sys
import time
from pathlib import Path

from arcatom_codex.native import composer_ready


def check_native(cwd, thread, remote=None):
    if os.name == "nt":
        return {"skipped": "POSIX PTY check; test Windows console handoff interactively"}
    import fcntl
    import pty
    import termios
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 110, 0, 0))
    env = {**os.environ, "TERM": "xterm-256color"}
    source = str(Path(__file__).resolve().parents[1])
    process = subprocess.Popen([sys.executable, "-m", "arcatom_codex.native", "--binary", "codex",
        "--cwd", cwd, "--thread", thread, "--command", "/status"] +
        (["--remote", remote] if remote else []),
        cwd=source, env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    output = b""
    entered = False
    status_seen = False
    exited = False
    trust_gate = False
    trust_at = None
    trust_exit_sent = False
    trust_cancel_count = 0
    ready_at = None
    entered_at = None
    deadline = time.monotonic() + 40
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                output = (output + data)[-100000:]
                if b"\x1b[6n" in data:
                    os.write(master, b"\x1b[1;1R")
                if b"\x1b[c" in data:
                    os.write(master, b"\x1b[?1;2c")
            now = time.monotonic()
            if ready_at is None and composer_ready(output):
                ready_at = now
            if ready_at and not entered and now - ready_at > 1:
                os.write(master, b"\r")
                entered, entered_at = True, now
                output = b""
            plain = re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", output).lower()
            if not trust_gate and b"trustthisfolder?" in re.sub(rb"\s+", b"", plain):
                # Never approve or persist a trust decision during the test.
                trust_gate = True
                trust_at = now
            if trust_at and not trust_exit_sent and now - trust_at > 1:
                os.write(master, b"\x1b")
                trust_exit_sent = True
            if trust_exit_sent and trust_cancel_count < 2 and now - trust_at > 2 + trust_cancel_count:
                # Shared TUI returns to its task list after declining trust.
                # 拒绝信任后退出任务列表，不确认信任或发送模型输入。
                os.write(master, b"\x03")
                trust_cancel_count += 1
            if entered and any(s in plain for s in (b"session:", b"session id:", b"context window:", b"token usage:")):
                status_seen = True
            if entered and not exited and (status_seen or now - entered_at > 8):
                # Read-only command test. Exit the official TUI and return to wrapper.
                os.write(master, b"\x15/quit\r")
                exited = True
            if process.poll() is not None:
                break
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        result = {"native_composer_detected": ready_at is not None,
                "native_status_executed": status_seen,
                "native_trust_gate_preserved": trust_gate,
                "native_exit_code": process.returncode}
        if process.returncode != 0 or not status_seen and not trust_gate:
            result["native_screen_excerpt"] = re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", output).decode(errors="replace")[-2400:]
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
