import argparse
import asyncio
import json
from pathlib import Path
import sys

from .rpc import CodexClient


async def check(cwd, binary):
    client = CodexClient(binary=binary, cwd=cwd)
    try:
        await client.start()
        response = await client.call("thread/list", {"limit": 1, "modelProviders": []})
        print(json.dumps({"ok": True, "handshake": "initialized", "thread_list": "ok",
                          "has_sessions": bool(response.get("data"))}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description="Arcatom Codex · 轻量终端会话工作台")
    parser.add_argument("--cwd", default=str(Path.cwd()), help="新会话的默认工作目录")
    parser.add_argument("--codex", default="codex", help="Codex 可执行文件")
    parser.add_argument("--demo", action="store_true", help="离线演示，不连接 Codex、不消耗用量")
    parser.add_argument("--check", action="store_true", help="只验证本机接口，不发送模型请求")
    args = parser.parse_args()
    cwd = str(Path(args.cwd).expanduser().resolve())
    if not Path(cwd).is_dir():
        parser.error("工作目录不存在")
    if args.check:
        raise SystemExit(asyncio.run(check(cwd, args.codex)))
    from .ui import ArcatomApp
    if args.demo:
        from .demo import DemoClient
        client = DemoClient()
    else:
        client = CodexClient(binary=args.codex, cwd=cwd)
    app = ArcatomApp(cwd=cwd, client=client, demo=args.demo)
    app.run()


if __name__ == "__main__":
    main()
