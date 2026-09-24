import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from textual.widgets import ContentSwitcher, Input, OptionList
from arcatom_codex.demo import DemoClient
from arcatom_codex.state import Store, rollout_usage, clean
from arcatom_codex.ui import ArcatomApp, Composer, Approval, Detail


class StateTests(unittest.TestCase):
    def test_stream_final_replaces_delta_and_is_thread_scoped(self):
        store = Store()
        for tid, text in [("a", "hello"), ("b", "world")]:
            store.event("item/agentMessage/delta", {"threadId": tid, "itemId": "same", "delta": text})
        store.event("item/completed", {"threadId": "a", "item": {"id": "same", "type": "agentMessage", "text": "hello!"}})
        self.assertEqual(store.get("a").items["same"]["text"], "hello!")
        self.assertEqual(store.get("b").items["same"]["text"], "world")

    def test_usage_is_latest_snapshot_not_sum_and_rejects_outside_home(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            log = home / "rollout.jsonl"
            lines = [json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"total_tokens": n, "input_tokens": n-10, "output_tokens": 10, "cached_input_tokens": 5}
            }}}) for n in [20, 30, 30]]
            log.write_text("\n".join(lines) + '\n{"type":')
            result = rollout_usage(str(log), home)
            self.assertEqual(result["total"]["totalTokens"], 30)
            self.assertEqual(result["total"]["cachedInputTokens"], 5)
            self.assertEqual(rollout_usage(str(log), home / "elsewhere"), {})

    def test_children_not_in_root_list(self):
        store = Store()
        store.merge({"id": "parent", "name": "Main", "updatedAt": 1})
        store.merge({"id": "child", "name": "Worker", "parentThreadId": "parent"})
        self.assertEqual([s.id for s in store.roots()], ["parent"])
        self.assertIn("child", store.get("parent").agents)

    def test_terminal_sequences_removed(self):
        self.assertEqual(clean("\x1b[31mtext\x1b[0m\x07"), "text")


class UiTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        client = DemoClient()
        return ArcatomApp(cwd=tempfile.gettempdir(), client=client, demo=True), client

    async def test_empty_left_and_draft_preservation(self):
        app, client = self.app()
        async with app.run_test(size=(110, 36)) as pilot:
            await pilot.pause(0.3)
            app.query_one("#sessions").focus()
            await pilot.press("enter")
            await pilot.pause(0.3)
            self.assertEqual(app.current, "demo-login")
            composer = app.query_one(Composer)
            await pilot.press("h", "i", "left")
            self.assertEqual(app.current, "demo-login")
            self.assertEqual(composer.text, "hi")
            await pilot.press("escape")
            self.assertIsNotNone(app.current)
            await pilot.press("escape")
            self.assertIsNone(app.current)
            app.query_one("#sessions").focus()
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertEqual(composer.text, "hi")
            composer.clear()
            await pilot.press("left")
            self.assertIsNone(app.current)
            self.assertEqual(app.query_one(ContentSwitcher).current, "home")

    async def test_search_arrow_and_multiline_stream(self):
        app, client = self.app()
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("up", "up", "enter")
            await pilot.pause(0.2)
            self.assertEqual(app.current, "demo-dashboard")
            await pilot.press("a", "ctrl+j", "b", "enter")
            await pilot.pause(1)
            sent = [p for m, p in client.calls if m == "turn/start"]
            self.assertEqual(sent[0]["input"][0]["text"], "a\nb")
            self.assertEqual(app.query_one(Composer).text, "")
            self.assertEqual(app.store.get("demo-dashboard").total, 28100)
            self.assertIsNone(app.store.get("demo-dashboard").active_turn)

    async def test_activity_modal_and_usage_with_background_events(self):
        app, client = self.app()
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(0.2)
            app.query_one("#sessions").focus()
            await pilot.press("enter")
            await pilot.pause(0.3)
            await pilot.press("ctrl+t")
            self.assertTrue(app.query_one("#activities").display)
            self.assertGreaterEqual(app.query_one("#activities", OptionList).option_count, 4)
            await pilot.press("enter")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, Detail)
            await client.events.put({"method": "thread/tokenUsage/updated", "params": {
                "threadId": "demo-login", "tokenUsage": {"total": {"totalTokens": 99}}}})
            await pilot.pause(0.2)
            await pilot.press("escape", "ctrl+u")
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, Detail)
            self.assertEqual(app.store.get("demo-login").total, 99)

    async def test_approval_never_automatic_and_escape_declines(self):
        app, client = self.app()
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(0.2)
            await client.events.put({"id": 55, "method": "item/commandExecution/requestApproval", "params": {
                "threadId": "demo-login", "command": "echo test", "reason": "test"}})
            await pilot.pause(0.3)
            self.assertIsInstance(app.screen, Approval)
            self.assertEqual(client.replies, [])
            await pilot.press("escape")
            await pilot.pause(0.2)
            self.assertEqual(client.replies, [(55, {"decision": "decline"})])

    async def test_small_terminal_and_new_session(self):
        app, client = self.app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("ctrl+n", "enter")
            await pilot.pause(0.3)
            self.assertIsNotNone(app.current)
            self.assertEqual(app.store.get(app.current).meta["cwd"], str(Path(tempfile.gettempdir()).resolve()))
            self.assertGreater(app.query_one("#transcript-scroll").size.height, 0)


if __name__ == "__main__":
    unittest.main()
