"""Read-only integration check; --prompt adds one minimal ephemeral model turn."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arcatom_codex.rpc import CodexClient
from arcatom_codex.state import Store, rollout_usage
from arcatom_codex.personal import PersonalSkill, turn_context


async def main(prompt, skills=False, commands=False, native=False):
    native_workspace = tempfile.TemporaryDirectory(prefix="arcatom-native-check-") if native else None
    client = CodexClient(shared=False, cwd=str(Path(native_workspace.name).resolve()) if native_workspace else str(Path(__file__).resolve().parents[1]))
    report = {}
    fixture = tempfile.TemporaryDirectory(prefix="arcatom-skill-check-") if skills else None
    disposable_thread = None
    try:
        await client.start()
        report["initialize"] = "ok"
        result = await client.call("thread/list", {"limit": 3, "modelProviders": []})
        threads = result.get("data", [])
        report["list_count"] = len(threads)
        if threads:
            meta = (await client.call("thread/read", {"threadId": threads[0]["id"], "includeTurns": True}))["thread"]
            store = Store()
            session = store.merge(meta, history=True)
            report["history_items"] = len(session.items)
            report["local_usage_available"] = bool(rollout_usage(meta.get("path")))
            children = await client.call("thread/list", {"ancestorThreadId": meta["id"], "sourceKinds": ["subAgent", "subAgentThreadSpawn", "subAgentOther"], "limit": 10, "modelProviders": []})
            report["subagent_list"] = "ok"
            report["subagent_count"] = len(children.get("data", []))
        if commands:
            models = []
            async for page in client.pages("model/list", {"includeHidden": False}):
                models.extend(page)
            report["model_count"] = len(models)
            initial = await client.call("thread/start", {"cwd": client.cwd, "ephemeral": True, "sandbox": "read-only", "approvalPolicy": "on-request"})
            test_id = initial["thread"]["id"]
            selected = next(m for m in models if m["model"] != initial["model"])
            effort = selected["defaultReasoningEffort"]
            await client.call("thread/settings/update", {"threadId": test_id, "model": selected["model"], "effort": effort})
            resumed = (await client.call("thread/read", {"threadId": test_id, "includeTurns": False}))["thread"]
            report["model_switch_confirmed"] = resumed["model"] == selected["model"] and resumed["reasoningEffort"] == effort
            await client.call("thread/settings/update", {"threadId": test_id, "collaborationMode": {"mode": "plan", "settings": {"model": selected["model"], "reasoning_effort": effort, "developer_instructions": None}}})
            report["plan_mode_update"] = "accepted"
            profiles = await client.call("permissionProfile/list", {"cwd": client.cwd})
            report["permission_profiles"] = len(profiles.get("data", []))
            await client.call("mcpServerStatus/list", {})
            report["mcp_list"] = "ok"
        if native and not prompt:
            from native_check import check_native
            started = await client.call("thread/start", {"cwd": client.cwd, "sandbox": "read-only", "approvalPolicy": "on-request"})
            disposable_thread = started["thread"]["id"]
            await client.call("thread/name/set", {"threadId": disposable_thread, "name": "ARCATOM native smoke — disposable"})
            await client.call("thread/unsubscribe", {"threadId": disposable_thread})
            report.update(await asyncio.to_thread(check_native, client.cwd, disposable_thread))
        if prompt:
            started = await client.call("thread/start", {"cwd": client.cwd, "ephemeral": not native, "sandbox": "read-only", "approvalPolicy": "on-request"})
            tid = started["thread"]["id"]
            if native:
                disposable_thread = tid
                await client.call("thread/name/set", {"threadId": tid, "name": "ARCATOM native smoke — disposable"})
            inputs = [{"type": "text", "text": "Reply with exactly ARCATOM_OK. Do not use tools. Do not inspect or change files."}]
            message_id = str(uuid.uuid4())
            params = {"threadId": tid, "clientUserMessageId": message_id, "input": inputs}
            if skills:
                path = Path(fixture.name) / "SKILL.md"
                path.write_text("---\nname: arcatom-smoke\ndescription: Read-only skill integration check.\n---\nThe test marker is ARCATOM_OK. Combine it with the suffix from application context. Do not use tools.")
                inputs[0]["text"] = "Use $arcatom-smoke. Reply with its test marker followed by the suffix from application context, no other text. Do not use tools."
                params["additionalContext"] = turn_context(
                    inputs[0]["text"], [PersonalSkill("arcatom-smoke", "Smoke check", path)],
                    "For this read-only check, append _CONTEXT to the skill's marker in your final response.")
            await client.call("turn/start", params)
            text = ""
            async with asyncio.timeout(90):
                while True:
                    event = await client.events.get()
                    if "id" in event:
                        # This test grants no tool approval.
                        if event["method"] in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                            await client.reply(event["id"], {"decision": "decline"})
                        else:
                            await client.reject_unsupported(event["id"])
                        continue
                    params = event.get("params", {})
                    if params.get("threadId") != tid:
                        continue
                    if event["method"] == "item/completed" and params["item"].get("type") == "agentMessage":
                        text += params["item"].get("text", "")
                    if event["method"] == "item/completed" and params["item"].get("type") == "userMessage":
                        report["client_message_id_matches"] = params["item"].get("clientId") == message_id
                    if event["method"] == "thread/tokenUsage/updated":
                        report["live_token_usage"] = bool(params.get("tokenUsage"))
                    if event["method"] == "turn/completed":
                        report["turn_status"] = params["turn"]["status"]
                        report["reply_matches"] = text.strip() == ("ARCATOM_OK_CONTEXT" if skills else "ARCATOM_OK")
                        if not report["reply_matches"]:
                            report["observed_reply"] = text[:400]
                        if skills:
                            report["external_skill_and_app_context"] = report["reply_matches"]
                        if params["turn"].get("error"):
                            report["turn_error"] = params["turn"]["error"]["message"]
                        break
            if native and report.get("reply_matches"):
                from native_check import check_native
                await client.call("thread/unsubscribe", {"threadId": tid})
                report.update(await asyncio.to_thread(check_native, client.cwd, tid))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        native_ok = not native or (report.get("native_status_executed") or report.get("native_trust_gate_preserved")) and report.get("native_exit_code") == 0
        return 0 if (not prompt or report.get("reply_matches")) and (not commands or report.get("model_switch_confirmed") and report.get("plan_mode_update") == "accepted") and native_ok else 1
    finally:
        if disposable_thread:
            # Only the ID returned by our own test thread/start can be removed.
            await client.call("thread/delete", {"threadId": disposable_thread})
        await client.close()
        if fixture:
            fixture.cleanup()
        if native_workspace:
            native_workspace.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", action="store_true", help="Send one minimal model request (uses tokens)")
    parser.add_argument("--skills", action="store_true", help="Check external skill references and app context (uses tokens)")
    parser.add_argument("--commands", action="store_true", help="Check model/settings/plan APIs on an ephemeral thread (no model request)")
    parser.add_argument("--native", action="store_true", help="Test official TUI on a disposable test chat, then remove it; add --prompt to populate its history")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.prompt or args.skills, args.skills, args.commands, args.native)))
