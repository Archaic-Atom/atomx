import asyncio
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest

from textual.widgets import Input, OptionList

from arcatom_codex.commands import BY_NAME, matches
from arcatom_codex.demo import DemoClient
from arcatom_codex.native import native_argv, terminal_reply
from arcatom_codex.pickers import Picker
from arcatom_codex.rpc import RpcError
from arcatom_codex.ui import ArcatomApp, Composer, Detail
from arcatom_codex.preferences import read_preferences, write_preferences


class CatalogTests(unittest.TestCase):
    def test_preferences_round_trip_and_corrupt_file_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            write_preferences({"status_fields": ["tokens", "model"], "code_theme": "monokai"}, path)
            self.assertEqual(read_preferences(path)["status_fields"], ["tokens", "model"])
            path.write_text("broken json")
            self.assertEqual(read_preferences(path), {})

    def test_installed_cli_command_contract_and_aliases(self):
        # Public enum and aliases of the user's installed 0.156.1 binary.
        expected = "model ide permissions keymap vim setup-default-sandbox experimental approve memories skills import hooks review rename new archive delete resume fork worktree app init compact recap plan voice goal agents side btw copy export raw tui diff mention status daemon warnings cd pwd cwd usage debug-config title statusline theme pets pet mcp apps plugins logout quit exit feedback rollout ps stop clean clear test-approval subagents debug-m-drop debug-m-update".split()
        self.assertFalse(set(expected) - BY_NAME.keys())
        self.assertEqual(matches("/mod")[0].name, "model")
        self.assertEqual(matches("/model hi"), [])
        self.assertEqual(matches("路径 /tmp"), [])
        self.assertEqual(matches("/does-not-exist"), [])

    def test_native_does_not_pass_slash_as_model_prompt(self):
        argv = native_argv("/path/codex", "/a directory", "a-thread")
        self.assertEqual(argv[-2:], ["resume", "a-thread"])
        self.assertNotIn("/hooks", argv)
        self.assertTrue(terminal_reply(b"\x1b[1;1R"))
        self.assertTrue(terminal_reply(b"\x1b]11;rgb:0000/0000/0000\x1b\\"))
        self.assertFalse(terminal_reply(b"\x1b[A"))
        self.assertFalse(terminal_reply(b"/plugins"))

    def test_native_pty_prefills_but_never_submits(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "fake-codex"
            fake.write_text(f"#!{sys.executable}\nimport os, tty\ntty.setraw(0)\nprint('› composer ? for shortcuts', flush=True)\ntext=b''\nwhile b'\\r' not in text and b'\\n' not in text:\n text+=os.read(0,1)\n if text==b'/hooks': print('DRAFT_READY', flush=True)\nprint('SUBMITTED_BY_USER', flush=True)\n")
            fake.chmod(0o755)
            master, slave = pty.openpty()
            process = subprocess.Popen([sys.executable, "-m", "arcatom_codex.native", "--binary", str(fake), "--cwd", directory, "--command", "/hooks"], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            output = b""
            try:
                deadline = time.monotonic() + 6
                while b"DRAFT_READY" not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        output += os.read(master, 65536)
                self.assertIn(b"DRAFT_READY", output)
                self.assertNotIn(b"SUBMITTED_BY_USER", output)
                self.assertIsNone(process.poll())
                os.write(master, b"\r")
                deadline = time.monotonic() + 5
                while process.poll() is None and time.monotonic() < deadline:
                    if select.select([master], [], [], .1)[0]:
                        try:
                            output += os.read(master, 65536)
                        except OSError:
                            break
                process.wait(timeout=2)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                os.close(master)


class CommandUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_archive_stays_hidden_after_late_status_event(self):
        client = DemoClient()
        app = ArcatomApp("/tmp", client=client, demo=True)
        async with app.run_test() as pilot:
            await self.open_chat(app, pilot)
            tid = app.current
            app.query_one(Composer).load_text("/archive")
            await pilot.press("enter")
            await pilot.pause(.2)
            app.store.event("thread/status/changed", {"threadId": tid, "status": {"type": "notLoaded"}})
            self.assertIsNone(app.current)
            self.assertNotIn(tid, [s.id for s in app.store.roots()])

    async def test_compact_and_stop_call_the_real_backend_actions(self):
        client = DemoClient()
        app = ArcatomApp("/tmp", client=client, demo=True)
        async with app.run_test() as pilot:
            await self.open_chat(app, pilot)
            app.query_one(Composer).load_text("/compact")
            await pilot.press("enter")
            await pilot.pause(.8)
            self.assertTrue(any(m == "thread/compact/start" for m, _ in client.calls))
            app.query_one(Composer).load_text("/stop")
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertTrue(any(m == "thread/backgroundTerminals/clean" for m, _ in client.calls))
            self.assertFalse(any(m == "turn/interrupt" for m, _ in client.calls))

    async def open_chat(self, app, pilot):
        await pilot.pause(.2)
        await pilot.press("down", "enter")
        await pilot.pause(.2)

    async def test_slash_filter_tab_escape_and_keyboard_model_selection(self):
        client = DemoClient()
        app = ArcatomApp("/tmp", client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await self.open_chat(app, pilot)
            await pilot.press("/", "m", "o", "d")
            self.assertTrue(app.query_one("#slash-commands").display)
            self.assertEqual(app.command_matches[0].name, "model")
            await pilot.press("tab")
            self.assertEqual(app.query_one(Composer).text, "/model")
            self.assertFalse(app.query_one("#slash-commands").display)
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, Picker)
            await pilot.press("down", "enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, Picker)
            await pilot.press("down", "enter")
            await pilot.pause(.2)
            self.assertEqual(app.store.get(app.current).meta["model"], "demo-reasoner")
            settings = [p for m, p in client.calls if m == "thread/settings/update"]
            self.assertEqual(settings[-1]["model"], "demo-reasoner")
            self.assertEqual(settings[-1]["effort"], "high")
            self.assertIn("demo-reasoner", app.query_one("#bottom")._Static__content.plain)
            self.assertFalse(any(m == "turn/start" for m, _ in client.calls))
            await pilot.press("/", "escape")
            self.assertFalse(app.query_one("#slash-commands").display)
            self.assertIsNotNone(app.current)
            app.query_one(Composer).load_text("新模型回复")
            await pilot.press("enter")
            await pilot.pause(.8)
            self.assertTrue(any(m == "turn/start" for m, _ in client.calls))

    async def test_model_cancel_and_backend_failure_do_not_claim_success(self):
        client = DemoClient()
        app = ArcatomApp("/tmp", client=client, demo=True)
        async with app.run_test() as pilot:
            await self.open_chat(app, pilot)
            app.query_one(Composer).load_text("/model")
            await pilot.press("enter")
            await pilot.pause(.2)
            await pilot.press("down", "enter", "escape")
            await pilot.pause(.2)
            self.assertFalse(any(m == "thread/settings/update" for m, _ in client.calls))
            before = app.store.get(app.current).meta["model"]
            original = client.call
            async def fail(method, params=None, timeout=30):
                if method == "thread/settings/update":
                    raise RpcError("settings denied")
                return await original(method, params, timeout)
            client.call = fail
            app.query_one(Composer).load_text("/model demo-reasoner")
            await pilot.press("enter")
            await pilot.pause(.2)
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertEqual(app.store.get(app.current).meta["model"], before)
            self.assertEqual(app.query_one(Composer).text, "/model demo-reasoner")

    async def test_home_slash_creates_session_and_opens_model_picker(self):
        app = ArcatomApp("/tmp", client=DemoClient(), demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await pilot.press("/", "m", "o", "d", "enter")
            await pilot.pause(.3)
            self.assertIsNotNone(app.current)
            self.assertIsInstance(app.screen, Picker)
            await pilot.press("escape")
            self.assertGreater(app.query_one("#transcript-scroll").size.height, 0)

    async def test_plan_model_updates_are_session_scoped_and_keep_plan(self):
        client = DemoClient()
        app = ArcatomApp("/tmp", client=client, demo=True)
        async with app.run_test() as pilot:
            await self.open_chat(app, pilot)
            app.query_one(Composer).load_text("/plan")
            await pilot.press("enter")
            await pilot.pause(.2)
            current = app.current
            self.assertEqual(app.store.get(current).meta["collaborationMode"]["mode"], "plan")
            app.query_one(Composer).load_text("/model demo-reasoner")
            await pilot.press("enter")
            await pilot.pause(.2)
            await pilot.press("enter")
            await pilot.pause(.2)
            settings = [p for m, p in client.calls if m == "thread/settings/update"][-1]
            self.assertEqual(settings["collaborationMode"]["settings"]["model"], "demo-reasoner")
            self.assertEqual(settings["collaborationMode"]["mode"], "plan")
            await pilot.press("left", "up", "enter")
            await pilot.pause(.2)
            self.assertNotEqual(app.current, current)
            self.assertNotEqual(app.store.get(app.current).meta.get("model"), "demo-reasoner")

    async def test_native_commands_are_labeled_and_demo_is_isolated(self):
        client = DemoClient()
        app = ArcatomApp("/tmp", client=client, demo=True)
        async with app.run_test() as pilot:
            await self.open_chat(app, pilot)
            app.query_one(Composer).load_text("/plugins")
            await pilot.pause(.1)
            self.assertTrue(app.command_matches[0].native)
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, Detail)
            self.assertIn("原生", app.screen.heading)
            self.assertFalse(any(m == "turn/start" for m, _ in client.calls))

    async def test_unknown_command_and_ordinary_slash_paths_are_preserved(self):
        client = DemoClient()
        app = ArcatomApp("/tmp", client=client, demo=True)
        async with app.run_test() as pilot:
            await self.open_chat(app, pilot)
            composer = app.query_one(Composer)
            composer.load_text("/unknown-command")
            await pilot.press("enter")
            await pilot.pause(.1)
            self.assertEqual(composer.text, "/unknown-command")
            composer.load_text("请查看 /tmp/file")
            await pilot.pause(.1)
            self.assertFalse(app.query_one("#slash-commands").display)


if __name__ == "__main__":
    unittest.main()
