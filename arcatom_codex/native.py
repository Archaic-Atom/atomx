"""Official Codex terminal handoff, with a command draft and no automatic Enter.

独立进程负责 PTY；避免在带工作线程的 Textual 进程中 fork。
"""

from __future__ import annotations

import argparse
import errno
import os
import re
import select
import signal
import subprocess
import sys
import time
from types import FrameType

from .i18n import tr
from .platform_support import executable_argv


def native_argv(
    binary: str, cwd: str, thread: str | None, remote: str | None = None
) -> list[str]:
    """Build argv for an official Codex terminal handoff. 构造原生终端接管参数。"""
    transport = ["--remote", remote] if remote else ["--no-daemon"]
    argv = [
        *executable_argv(binary),
        *transport,
        "--no-alt-screen",
        "--cd",
        cwd,
    ]
    if thread:
        argv.extend(["resume", thread])
    return argv


def composer_ready(output: bytes) -> bool:
    """Detect the native input prompt before prefilling a draft.

    检测原生输入框后才填入草稿。
    """
    text = re.sub(rb"\x1b\[[0-?]*[ -/]*[@-~]", b"", output).decode(
        errors="ignore"
    )
    return ("›" in text or "❯" in text) and any(
        s in text.lower()
        for s in ("for shortcuts", "context left", "context remaining")
    )


def terminal_reply(data: bytes) -> bool:
    """Recognize terminal probes without mistaking them for user keystrokes.

    识别终端探测响应，避免当作用户输入。
    """
    return bool(
        re.fullmatch(
            rb"(?:\x1b\[[?>]?[0-9;]*[Rcu]|\x1b\](?:10|11|12);[^\x07]*(?:\x07|\x1b\\))+",
            data,
        )
    )


def bridge(argv: list[str], command: str) -> int:
    """Forward a terminal, prefilling only after the real composer is visible.

    转发终端内容，输入框可见后才预填命令。
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError(
            tr("原生命令需要交互终端，请在系统终端中运行 atomx。")
        )
    if "\n" in command or "\r" in command or not command.startswith("/"):
        raise ValueError(tr("原生命令必须为单行斜杠命令"))
    if os.name == "nt":
        # Inherit the Windows console. No Unix PTY or simulated keystrokes.
        # Windows 直接接管控制台；用户在官方输入框中输入已选命令。
        print(
            tr("Windows 原生界面打开后，请输入 {0} 并按 Enter。").format(
                command
            ),
            flush=True,
        )
        return subprocess.call(argv)
    import fcntl
    import pty
    import termios
    import tty

    stdin, stdout = sys.stdin.fileno(), sys.stdout.fileno()
    saved = termios.tcgetattr(stdin)
    pid, master = pty.fork()
    if pid == 0:
        os.execvp(argv[0], argv)
    old_handler = signal.getsignal(signal.SIGWINCH)
    old_term = signal.getsignal(signal.SIGTERM)

    def terminate(signum: int, _frame: FrameType | None) -> None:
        """Exit through cleanup when termination is requested. 终止时通过清理路径退出。"""
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)

    def resize(*_: object) -> None:
        """Forward the parent terminal dimensions to the child PTY.

        将终端尺寸同步到子进程。
        """
        try:
            fcntl.ioctl(
                master,
                termios.TIOCSWINSZ,
                fcntl.ioctl(stdin, termios.TIOCGWINSZ, b"\0" * 8),
            )
        except OSError:
            pass

    signal.signal(signal.SIGWINCH, resize)
    resize()
    recent = b""
    sent = False
    typing = False
    status = None
    try:
        tty.setraw(stdin)
        while True:
            readable, _, _ = select.select([master, stdin], [], [], 0.1)
            if master in readable:
                try:
                    data = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                os.write(stdout, data)
                recent = (recent + data)[-32768:]
                if not sent and not typing and composer_ready(recent):
                    # Draft only. Native workflow retains its own confirmation steps.
                    # 只填入草稿，原生流程保留自身的确认步骤。
                    os.write(master, command.encode())
                    sent = True
            if stdin in readable:
                data = os.read(stdin, 65536)
                if not data:
                    break
                # Cursor position / device attribute responses are not user typing.
                # 光标位置和设备属性响应不属于用户输入。
                if not terminal_reply(data):
                    typing = True
                os.write(master, data)
            done, child_status = os.waitpid(pid, os.WNOHANG)
            if done:
                status = child_status
                break
    finally:
        termios.tcsetattr(stdin, termios.TCSANOW, saved)
        signal.signal(signal.SIGWINCH, old_handler)
        signal.signal(signal.SIGTERM, old_term)
        os.close(master)
        if status is None:
            try:
                done, status = os.waitpid(pid, os.WNOHANG)
                if not done:
                    os.kill(pid, signal.SIGHUP)
                    deadline = time.monotonic() + 2
                    while not done and time.monotonic() < deadline:
                        time.sleep(0.05)
                        done, status = os.waitpid(pid, os.WNOHANG)
                    if not done:
                        os.kill(pid, signal.SIGKILL)
                        _, status = os.waitpid(pid, 0)
            except ChildProcessError:
                status = 0
    return os.waitstatus_to_exitcode(status)


def main() -> None:
    """Parse command-line options and run the selected entry point. 解析命令行并启动。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--thread")
    parser.add_argument("--remote")
    parser.add_argument("--command", required=True)
    args = parser.parse_args()
    print(
        tr(
            (
                "官方 Codex · {0}\n命令就绪后按 Enter；若出现登录/信任"
                "提示，完成后输入该命令。\n使用 /quit 退出官方界面即可返回 Ato"
                "mX。"
            )
        ).format(args.command),
        flush=True,
    )
    raise SystemExit(
        bridge(
            native_argv(args.binary, args.cwd, args.thread, args.remote),
            args.command,
        )
    )


if __name__ == "__main__":
    main()
