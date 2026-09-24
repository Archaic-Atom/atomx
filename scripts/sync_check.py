"""Check two shared clients without a model request. 双客户端共享服务验证。"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arcatom_codex.rpc import CodexClient


async def main(native=False):
    clients = [CodexClient(cwd=tempfile.gettempdir()) for _ in range(2)]
    tid = None
    try:
        results = await asyncio.gather(*(client.start() for client in clients), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        started = await clients[0].call("thread/start", {
            "cwd": tempfile.gettempdir(),
            "sandbox": "read-only", "approvalPolicy": "on-request"})
        tid = started["thread"]["id"]
        await clients[0].call("thread/inject_items", {"threadId": tid, "items": [{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "ARCATOM_SYNC_FIXTURE"}]}]})
        resumed = await clients[1].call("thread/resume", {"threadId": tid, "excludeTurns": True})
        await clients[0].call("thread/name/set", {"threadId": tid, "name": "arcatom-sync-check"})
        async with asyncio.timeout(10):
            while True:
                message = await clients[1].events.get()
                if message.get("method") == "thread/name/updated" and message.get("params", {}).get("threadId") == tid:
                    break
        await clients[0].call("thread/shellCommand", {
            "threadId": tid, "command": "printf ARCATOM_SYNC_FIXTURE", "timeoutMs": 1000})
        async with asyncio.timeout(15):
            while True:
                message = await clients[1].events.get()
                if message.get("method") == "turn/completed" and message.get("params", {}).get("threadId") == tid:
                    break
        page = await clients[1].call("thread/items/list", {
            "threadId": tid, "limit": 40, "sortDirection": "desc"})
        assert any("ARCATOM_SYNC_FIXTURE" in str(entry) for entry in page["data"])
        if native:
            from native_check import check_native
            result = await asyncio.to_thread(check_native, tempfile.gettempdir(), tid, clients[0].remote_endpoint)
            print(json.dumps({key: value for key, value in result.items() if key != "native_screen_excerpt"}))
            assert result.get("native_status_executed") or result.get("native_trust_gate_preserved")
            assert result.get("native_exit_code") == 0
        await clients[0].close()
        read = await clients[1].call("thread/read", {"threadId": tid})
        print(json.dumps({"two_clients_same_thread": resumed["thread"]["id"] == tid,
                          "rename_notification_received": True, "paged_content_matches": True, "live_turn_received": True,
                          "second_client_survives_first_close": read["thread"]["id"] == tid,
                          "model_requests": 0}))
    finally:
        if tid:
            for client in clients:
                if not client.closing and (client.websocket is not None or
                                          client.process and client.process.returncode is None):
                    await client.call("thread/delete", {"threadId": tid})
                    break
        await asyncio.gather(*(client.close() for client in clients))


if __name__ == "__main__":
    asyncio.run(main(native="--native" in sys.argv))
