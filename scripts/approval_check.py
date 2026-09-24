"""Verify reviewer changes in an ephemeral thread without any model request."""
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arcatom_codex.rpc import CodexClient


async def main():
    client = CodexClient(shared=False, cwd=str(Path(__file__).resolve().parents[1]))
    tid = None
    try:
        await client.start()
        created = await client.call("thread/start", {
            "cwd": client.cwd, "ephemeral": True, "sandbox": "read-only",
            "approvalPolicy": "on-request", "approvalsReviewer": "auto_review"})
        tid = created["thread"]["id"]
        assert created["approvalsReviewer"] == "auto_review", created["approvalsReviewer"]
        assert created["approvalPolicy"] == "on-request", created["approvalPolicy"]
        await client.call("thread/settings/update", {
            "threadId": tid, "approvalPolicy": "on-request", "approvalsReviewer": "user"})
        await client.call("thread/settings/update", {
            "threadId": tid, "approvalPolicy": "on-request", "approvalsReviewer": "auto_review"})
        # Empty threads have no rollout to resume until a model turn exists.
        # Verify update acknowledgements without spending model usage.
        print(json.dumps({"new_auto_review": created["approvalsReviewer"],
                          "manual_update": "accepted", "auto_update": "accepted",
                          "sandbox": created["sandbox"]["type"], "model_requests": 0}))
    finally:
        try:
            if tid:
                await client.call("thread/unsubscribe", {"threadId": tid})
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
