"""Regressions for deletion, delayed sending, and personal status preferences."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from textual.widgets import OptionList

from arcatom_codex.demo import DemoClient
from arcatom_codex.personal import discover_skills, mentioned_skills, turn_context
from arcatom_codex.rpc import RpcError
from arcatom_codex.state import Session, Store
from arcatom_codex.statusline import build_status
from arcatom_codex.ui import ArcatomApp, Composer


class PausedClient(DemoClient):
    def __init__(self, method, fail=False):
        super().__init__()
        self.paused_method = method
        self.fail = fail
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, method, params=None, timeout=30):
        if method == self.paused_method:
            self.entered.set()
            await self.release.wait()
            if self.fail:
                raise RpcError("模拟后端失败")
        return await super().call(method, params, timeout)


class PreferencesTests(unittest.TestCase):
    def test_remaining_status_uses_latest_context_and_hides_missing(self):
        session = Session("test", meta={"model": "Codex"}, usage={
            "total": {"totalTokens": 1500000},
            "last": {"totalTokens": 250}, "modelContextWindow": 1000})
        limits = {"rateLimits": {
            "primary": {"usedPercent": 24, "windowDurationMins": 300, "resetsAt": 4600},
            "secondary": {"usedPercent": 90, "windowDurationMins": 10080, "resetsAt": 91400}}}
        status = build_status(session, limits, 120, now=1000).plain
        self.assertIn("tok:1.5M │ ctx:75% │ 5h:76% 1h 0m │ 7d:10% 1d 1h │ Codex", status)
        self.assertNotIn("ctx:", build_status(None, {}, 80, now=1000).plain)
        self.assertNotIn("5h:", build_status(None, {}, 80, now=1000).plain)
        self.assertIn("\n", build_status(session, limits, 40, now=1000).plain)

    def test_source_symlinks_and_explicit_mentions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root.resolve() / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("---\nname: sample\ndescription: >\n  Draw figures\n  clearly.\n---\nBody")
            skills = root / "skills"
            skills.mkdir()
            try:
                (skills / "sample").symlink_to(source, target_is_directory=True)
                (skills / "synced").symlink_to(source, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Directory symlinks unavailable: {error}")
            result = discover_skills(skills)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].description, "Draw figures clearly.")
            self.assertEqual(result[0].path, source / "SKILL.md")
            self.assertEqual(mentioned_skills("用 $sample 来做", result)[0]["path"], str(source / "SKILL.md"))
            self.assertEqual(mentioned_skills("$sample-other", result), [])
            (source / "SKILL.md").write_text("Updated source instruction")
            context = turn_context("$sample", result, "Catalog")
            self.assertIn("Updated source instruction", context["arcatom-skill-sample"]["value"])
            self.assertIn(str(source), context["arcatom-skill-sample"]["value"])

    def test_deleted_descendants_do_not_return_from_late_events(self):
        store = Store()
        store.merge({"id": "a"})
        store.merge({"id": "b", "parentThreadId": "a"})
        self.assertEqual(store.remove_thread("a"), {"a", "b"})
        store.event("turn/completed", {"threadId": "a", "turn": {"id": "t"}})
        store.merge({"id": "c", "parentThreadId": "a"})
        store.get("a")
        self.assertEqual(store.sessions, {})


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_selected_only_once_and_keep_neighbor(self):
        client = PausedClient("thread/delete")
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await pilot.press("down", "down", "ctrl+x", "ctrl+x")
            await asyncio.wait_for(client.entered.wait(), 2)
            self.assertIn("demo-dashboard", app.store.sessions)
            client.release.set()
            await pilot.pause(.3)
            self.assertNotIn("demo-dashboard", app.store.sessions)
            self.assertIn("demo-login", app.store.sessions)
            self.assertEqual(len([m for m, _ in client.calls if m == "thread/delete"]), 1)
            options = app.query_one("#sessions", OptionList)
            self.assertEqual(options.get_option_at_index(options.highlighted).id, "demo-readme")

    async def test_delete_failure_keeps_history(self):
        client = PausedClient("thread/delete", fail=True)
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await pilot.press("down", "down", "ctrl+x")
            client.release.set()
            await pilot.pause(.2)
            self.assertIn("demo-dashboard", app.store.sessions)
            self.assertFalse(app.deleting)

    async def test_immediate_echo_during_resume_and_no_duplicate(self):
        client = PausedClient("thread/resume")
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await pilot.press("down", "down", "enter")
            await pilot.pause(.2)
            composer = app.query_one(Composer)
            composer.load_text("测试发送")
            await pilot.press("enter")
            await asyncio.wait_for(client.entered.wait(), 2)
            self.assertEqual(composer.text, "")
            session = app.store.get("demo-dashboard")
            self.assertEqual(len(session.pending_messages), 1)
            self.assertTrue(app.query_one("#waiting").display)
            before = app.query_one("#waiting")._Static__content.plain
            await pilot.pause(.2)
            after = app.query_one("#waiting")._Static__content.plain
            self.assertNotEqual(before, after)
            self.assertGreater(app.query_one("#transcript-scroll").size.height, 0)
            await pilot.press("left", "enter")
            client.release.set()
            await pilot.pause(1)
            self.assertEqual(len([i for i in session.items.values() if i["type"] == "userMessage"]), 1)
            self.assertFalse(session.pending_messages)
            self.assertFalse(app.query_one("#waiting").display)
            await pilot.press("ctrl+x")
            self.assertFalse(any(m == "thread/delete" for m, _ in client.calls))

    async def test_send_failure_restores_draft_without_retry(self):
        client = PausedClient("thread/resume", fail=True)
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await pilot.press("down", "down", "enter")
            await pilot.pause(.2)
            app.query_one(Composer).load_text("保留这条消息")
            await pilot.press("enter")
            await asyncio.wait_for(client.entered.wait(), 2)
            client.release.set()
            await pilot.pause(.3)
            self.assertEqual(app.query_one(Composer).text, "保留这条消息")
            self.assertFalse(app.query_one("#waiting").display)
            self.assertFalse(any(m == "turn/start" for m, _ in client.calls))


if __name__ == "__main__":
    unittest.main()
