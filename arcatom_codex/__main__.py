"""Command-line entry points and connection checks. 命令行入口与连接检查。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .backend.rpc import CodexClient
from .i18n import tr


async def check(cwd: str, binary: str) -> int:
    """Verify the handshake and list access without a model request. 只读检查连接。"""
    client = CodexClient(binary=binary, cwd=cwd)
    try:
        await client.start()
        response = await client.call(
            "thread/list", {"limit": 1, "modelProviders": []}
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "handshake": "initialized",
                    "thread_list": "ok",
                    "has_sessions": bool(response.get("data")),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    finally:
        await client.close()


def main() -> None:
    """Parse command-line options and run the selected entry point. 解析命令行并启动。"""
    parser = argparse.ArgumentParser(
        description=tr("AtomX · 轻量终端会话工作台")
    )
    parser.add_argument(
        "--version", action="version", version=f"AtomX {__version__}"
    )
    parser.add_argument(
        "--cwd", default=str(Path.cwd()), help=tr("新会话的默认工作目录")
    )
    parser.add_argument("--codex", default="codex", help=tr("Codex 可执行文件"))
    parser.add_argument(
        "--demo",
        action="store_true",
        help=tr("离线演示，不连接 Codex、不消耗用量"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=tr("只验证本机接口，不发送模型请求"),
    )
    parser.add_argument(
        "--keyboard-mode",
        choices=("standard", "enhanced"),
        help="Override terminal keyboard protocol (default: standard)",
    )
    args = parser.parse_args()
    cwd = str(Path(args.cwd).expanduser().resolve())
    if not Path(cwd).is_dir():
        parser.error(tr("工作目录不存在"))
    if args.check:
        raise SystemExit(asyncio.run(check(cwd, args.codex)))
    from .ui import ArcatomApp

    client: CodexClient | DemoClient
    if args.demo:
        from .backend.demo import DemoClient

        client = DemoClient()
    else:
        client = CodexClient(binary=args.codex, cwd=cwd)
    app = ArcatomApp(
        cwd=cwd,
        client=client,
        demo=args.demo,
        enhanced_keyboard=(
            None
            if args.keyboard_mode is None
            else args.keyboard_mode == "enhanced"
        ),
    )
    app.run()


if __name__ == "__main__":
    main()
