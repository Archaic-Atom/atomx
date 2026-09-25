"""Workspace behavior across themes, keyboard focus and operating systems."""
import asyncio
import ast
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from textual import events
from textual.widgets import Input, OptionList, Select, Switch
from textual.document._document import Selection

from arcatom_codex.appearance import PALETTES, palette_for
from arcatom_codex.demo import DemoClient
from arcatom_codex.native import bridge
from arcatom_codex.platform_support import executable_argv
from arcatom_codex.settings import Settings
from arcatom_codex.state import Store
from arcatom_codex.ui import ArcatomApp, Composer
from arcatom_codex.i18n import ENGLISH, tr
from arcatom_codex.clipboard import ClipboardContent, read_clipboard
from PIL import Image


class WorkspaceStateTests(unittest.TestCase):
    def test_translation_catalog_covers_literal_ui_strings(self):
        package = Path(__file__).resolve().parents[1] / "arcatom_codex"
        for path in package.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "tr" and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    self.assertIn(node.args[0].value, ENGLISH, str(path))
        self.assertEqual(tr("新会话", "en"), "New session")
        self.assertEqual(tr("新会话", "zh"), "新会话")

    def test_clipboard_image_is_saved_as_a_real_attachment(self):
        source = Path(__file__).resolve().parents[1] / "arcatom_codex/assets/logo-dark.png"
        with Image.open(source) as image, tempfile.TemporaryDirectory() as directory:
            with patch("arcatom_codex.clipboard.ImageGrab.grabclipboard", return_value=image):
                content = read_clipboard(Path(directory))
            self.assertEqual(len(content.images), 1)
            with Image.open(content.images[0]) as saved:
                self.assertEqual(saved.size, image.size)
                self.assertEqual(saved.format, "PNG")

    def test_waiting_working_history_lifecycle(self):
        store = Store()
        session = store.merge({"id": "a", "status": {"type": "idle"}})
        self.assertEqual(session.section, "history")
        store.event("turn/started", {"threadId": "a", "turn": {"id": "t"}})
        self.assertEqual(session.section, "working")
        store.event("thread/status/changed", {"threadId": "a", "status": {
            "type": "active", "activeFlags": ["waitingOnApproval"]}})
        self.assertEqual(session.section, "waiting")
        store.event("thread/status/changed", {"threadId": "a", "status": {
            "type": "active", "activeFlags": []}})
        self.assertEqual(session.section, "working")
        session.pending_requests.add("approval")
        self.assertEqual(session.section, "waiting")
        session.pending_requests.clear()
        store.event("turn/completed", {"threadId": "a", "turn": {"id": "t"}})
        self.assertEqual(session.section, "waiting")
        store.event("turn/started", {"threadId": "a", "turn": {"id": "next"}})
        self.assertEqual(session.section, "working")

    def test_windows_npm_entry_keeps_arguments_out_of_shell(self):
        with tempfile.TemporaryDirectory(prefix="codex & space ") as directory:
            root = Path(directory)
            entry = root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("// test", encoding="utf-8")
            with patch("arcatom_codex.platform_support.shutil.which", side_effect=lambda name:
                       str(root / "codex.cmd") if name == "codex" else "node.exe"):
                self.assertEqual(executable_argv("codex", windows=True), ["node.exe", str(entry)])
            with patch("arcatom_codex.native.os.name", "nt"), \
                 patch("sys.stdin.isatty", return_value=True), \
                 patch("sys.stdout.isatty", return_value=True), \
                 patch("arcatom_codex.native.subprocess.call", return_value=0) as run:
                self.assertEqual(bridge(["codex.exe", "--cd", directory], "/hooks"), 0)
                run.assert_called_once_with(["codex.exe", "--cd", directory])


class WorkspaceUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clipboard = patch("arcatom_codex.ui.copy_text", return_value=True)
        self.copy_writer = clipboard.start()
        self.addCleanup(clipboard.stop)

    async def test_escape_browse_double_arrows_and_enter_to_edit_without_sending(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await pilot.press("enter")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.ingest({"id": "long", "type": "agentMessage", "text": "Line\n\n" * 100})
            app.paint(force=True)
            await pilot.pause(.2)
            composer = app.query_one(Composer)
            composer.load_text("Do not send while navigating")
            scroll = app.query_one("#transcript-scroll")
            scroll.scroll_end(animate=False)
            await pilot.press("escape")
            self.assertIsNotNone(app.current)
            self.assertTrue(scroll.has_focus)
            self.assertTrue(composer.read_only)
            # Queue a real input burst; pilot.press waits for rendering between keys.
            # 按键到达间隔由事件时间决定，不能用测试渲染耗时模拟双击。
            first = events.Key("up", None)
            second = events.Key("up", None)
            second.time = first.time + .1
            app.post_message(first)
            app.post_message(second)
            await pilot.pause(.1)
            self.assertEqual(scroll.scroll_y, 0)
            first = events.Key("down", None)
            second = events.Key("down", None)
            second.time = first.time + .1
            app.post_message(first)
            app.post_message(second)
            await pilot.pause(.1)
            self.assertTrue(scroll.is_vertical_scroll_end)
            self.assertTrue(scroll.has_focus)
            await pilot.press("down")
            self.assertTrue(composer.has_focus)
            self.assertTrue(composer.read_only)
            await pilot.press("enter")
            self.assertFalse(composer.read_only)
            self.assertEqual(composer.text, "Do not send while navigating")
            self.assertFalse(any(m == "turn/start" for m, _ in client.calls))
            await pilot.press("escape", "escape")
            self.assertIsNone(app.current)
            self.assertEqual(session.draft, "Do not send while navigating")

    async def test_empty_enter_new_history_recall_and_focus_return(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await pilot.press("ctrl+l", "enter")
            await pilot.pause(.2)
            self.assertEqual(sum(m == "thread/start" for m, _ in client.calls), 1)
            self.assertIsNotNone(app.current)
            session = app.store.get(app.current)
            for index, prompt in enumerate(("older input", "latest input")):
                session.ingest({"id": str(index), "type": "userMessage",
                                "content": [{"type": "text", "text": prompt}]})
            composer = app.query_one(Composer)
            composer.load_text("unsent draft")
            await pilot.press("up")
            self.assertEqual(composer.text, "latest input")
            await pilot.press("up")
            self.assertEqual(composer.text, "older input")
            await pilot.press("down", "down")
            self.assertEqual(composer.text, "unsent draft")
            await pilot.press("pageup")
            self.assertTrue(app.query_one("#transcript-scroll").has_focus)
            await pilot.press("ctrl+l")
            self.assertTrue(composer.has_focus)
            await pilot.press("pageup", "left")
            self.assertIsNone(app.current)
            self.assertEqual(session.draft, "unsent draft")
            await pilot.press("up", "up", "enter")
            await pilot.pause(.2)
            self.assertEqual(sum(m == "thread/start" for m, _ in client.calls), 1)

    async def test_language_switch_preserves_draft_and_copy_does_not_interrupt(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            self.assertEqual(app.language, "en")
            self.assertIn("New session", str(app.query_one("#new-session").label))
            app.query_one("#sessions").focus()
            await pilot.press("enter")
            await pilot.pause(.2)
            composer = app.query_one(Composer)
            composer.load_text("保留中文草稿")
            composer.selection = Selection((0, 0), (0, 2))
            await pilot.press("ctrl+c")
            self.assertEqual(app.clipboard, "保留")
            self.assertFalse(any(m == "turn/interrupt" for m, _ in client.calls))
            composer.selection = Selection.cursor((0, 0))
            await pilot.press("f3")
            self.assertIn("我已把检查", app.clipboard)
            await pilot.press("f2")
            app.screen.query_one("#language", Select).value = "zh"
            await pilot.pause(.1)
            await pilot.press("ctrl+s")
            await pilot.pause(.2)
            self.assertEqual(app.language, "zh")
            self.assertEqual(app.view_preferences["language"], "zh")
            self.assertIn("新会话", str(app.query_one("#new-session").label))
            self.assertEqual(composer.text, "保留中文草稿")
            await pilot.press("f2")
            app.screen.query_one("#language", Select).value = "en"
            await pilot.pause(.1)
            await pilot.press("ctrl+s")
            await pilot.pause(.1)
            self.assertEqual(app.language, "en")

    async def test_pasted_image_is_sent_and_failure_restores_attachments(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await pilot.press("up", "up", "enter")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            path = str(Path(__file__).resolve().parents[1] / "arcatom_codex/assets/logo-dark.png")
            with patch("arcatom_codex.ui.read_clipboard", return_value=ClipboardContent(images=[path])):
                await pilot.press("ctrl+v")
                await pilot.pause(.2)
            self.assertEqual(session.attachments, [path])
            self.assertTrue(app.query_one("#attachments").display)
            await pilot.press("enter")
            await pilot.pause(.8)
            sent = [p for m, p in client.calls if m == "turn/start"]
            self.assertEqual(sent[-1]["input"], [{"type": "localImage", "path": path}])
            self.assertEqual(session.attachments, [])
            self.assertEqual(len([i for i in session.items.values() if i["type"] == "userMessage"]), 1)
            async def fail(*args, **kwargs):
                raise RuntimeError("delivery failed")
            with patch.object(client, "call", side_effect=fail):
                session.attachments.append(path)
                app.query_one(Composer).load_text("keep this draft")
                await pilot.press("enter")
                await pilot.pause(.2)
                self.assertEqual(session.attachments, [path])
                self.assertEqual(app.query_one(Composer).text, "keep this draft")

    async def test_settings_preview_cancel_save_and_new_directory(self):
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        with tempfile.TemporaryDirectory() as directory:
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause(.2)
                await pilot.press("f2")
                self.assertIsInstance(app.screen, Settings)
                app.screen.query_one("#ui-theme", Select).value = "paper"
                await pilot.pause(.2)
                self.assertEqual(app.palette.background, PALETTES["paper"].background)
                await pilot.press("escape")
                await pilot.pause(.1)
                self.assertEqual(app.palette.background, PALETTES["gray"].background)
                await pilot.press("f2")
                for name in PALETTES:
                    app.screen.query_one("#ui-theme", Select).value = name
                    await pilot.pause(.05)
                    self.assertEqual(app.palette, replace(palette_for({"ui_theme": name}),
                                                          foreground="default"))
                app.screen.query_one("#accent", Select).value = "violet"
                app.screen.query_one("#follow-output", Switch).value = False
                app.screen.query_one("#default-cwd", Input).value = directory
                await pilot.press("ctrl+s")
                await pilot.pause(.2)
                self.assertEqual(app.view_preferences["accent"], "violet")
                self.assertFalse(app.view_preferences["follow_output"])
                await pilot.press("ctrl+n", "enter")
                await pilot.pause(.2)
                self.assertEqual(app.store.get(app.current).meta["cwd"], str(Path(directory).resolve()))

    async def test_keyboard_browse_stream_and_preserve_multiline_draft(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            app.query_one("#sessions").focus()
            await pilot.press("enter")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.ingest({"id": "long", "type": "agentMessage", "text": "context\n\n" * 60})
            app.paint(force=True)
            scroll = app.query_one("#transcript-scroll")
            await pilot.pause(.2)
            scroll.scroll_end(animate=False)
            await pilot.pause(.1)
            bottom = scroll.scroll_y
            await pilot.press("pageup")
            await pilot.pause(.1)
            self.assertTrue(scroll.has_focus)
            self.assertLess(scroll.scroll_y, bottom)
            position = scroll.scroll_y
            session.items["long"]["text"] += "more output\n\n" * 10
            app.paint(force=True)
            await pilot.pause(.1)
            self.assertEqual(scroll.scroll_y, position)
            await pilot.press("end", "down", "enter")
            await pilot.pause(.1)
            self.assertTrue(app.query_one(Composer).has_focus)
            await pilot.press("pageup", "ctrl+l")
            self.assertEqual(app.current, "demo-login")
            composer = app.query_one(Composer)
            self.assertTrue(composer.has_focus)
            composer.load_text("first\nsecond")
            composer.move_cursor((1, 4))
            await pilot.press("up")
            self.assertTrue(composer.has_focus)
            self.assertEqual(composer.cursor_location[0], 0)
            await pilot.press("pageup", "ctrl+l")
            self.assertEqual(composer.text, "first\nsecond")

    async def test_group_headers_cannot_open_or_delete_and_selection_is_stable(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            options = app.query_one("#sessions", OptionList)
            self.assertEqual(sum(options.get_option_at_index(i).disabled
                                 for i in range(options.option_count)), 3)
            self.assertEqual(options.get_option_at_index(options.highlighted).id, "demo-login")
            await pilot.press("up", "up", "up")
            app.store.event("turn/started", {"threadId": "demo-dashboard", "turn": {"id": "t"}})
            app.paint(force=True)
            self.assertEqual(options.get_option_at_index(options.highlighted).id, "demo-login")
            options.highlighted = next(i for i in range(options.option_count)
                                       if options.get_option_at_index(i).id == "demo-dashboard")
            await pilot.press("ctrl+x")
            self.assertFalse(any(m == "thread/delete" for m, _ in client.calls))
            for thread in list(app.store.roots()):
                app.store.remove_thread(thread.id)
            app.paint(force=True)
            await pilot.press("ctrl+x")
            self.assertIsNone(app.current)
            self.assertIsNone(options.highlighted)
            await pilot.click("#new-session")
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertIsNotNone(app.current)

    async def test_settings_slash_does_not_create_a_thread(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await pilot.press("/", "s", "e", "t", "enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, Settings)
            self.assertFalse(any(m == "thread/start" for m, _ in client.calls))
