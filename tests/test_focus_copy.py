"""User-visible focus, nested settings, typing and mouse-copy regressions."""
import tempfile
import unittest
from unittest.mock import patch

from textual.content import Content
from textual._xterm_parser import XTermParser
from textual.geometry import Offset
from textual.selection import Selection
from textual.widgets import Select, Switch

from arcatom_codex.demo import DemoClient
from arcatom_codex.settings import Settings, DirectoryInput
from arcatom_codex.ui import ArcatomApp, Composer


class FocusCopyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        writer = patch("arcatom_codex.ui.copy_text", return_value=True)
        self.writer = writer.start()
        self.addCleanup(writer.stop)

    def app(self):
        return ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)

    async def test_home_arrows_stay_in_sessions_and_typing_wakes_input(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            search, sessions = app.query_one("#search"), app.query_one("#sessions")
            self.assertTrue(sessions.has_focus)
            first = sessions.selectable_indices()[0]
            self.assertEqual(sessions.highlighted, first)
            self.assertEqual(app.palette.accent, "#e5a277")
            self.assertGreater(search.region.y, sessions.region.y)
            self.assertEqual(search.region.height, 5)
            self.assertFalse(app.query_one("#new-session").can_focus)
            self.assertFalse(app.query_one("#settings").can_focus)
            await pilot.press("up")
            self.assertEqual(sessions.highlighted, sessions.selectable_indices()[-1])
            await pilot.press("down")
            self.assertEqual(sessions.highlighted, first)
            self.assertTrue(sessions.has_focus)
            await pilot.press("d")
            self.assertTrue(search.has_focus)
            self.assertEqual(search.value, "d")
            self.assertIsNone(sessions.highlighted)
            await pilot.press("backspace", "down")
            self.assertTrue(sessions.has_focus)
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertEqual(app.current, "demo-login")
            await pilot.press("escape", "escape")
            self.assertTrue(sessions.has_focus)
            await pilot.press("ctrl+l", "enter")
            await pilot.pause(.2)
            self.assertEqual(sum(m == "thread/start" for m, _ in app.client.calls), 1)

    async def test_settings_arrows_navigate_enter_edits_escape_returns_one_layer(self):
        app = self.app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await pilot.press("f2")
            settings = app.screen
            language = settings.query_one("#language", Select)
            await pilot.press("down")
            self.assertEqual(settings.focused.id, "approve-for-me")
            self.assertEqual(language.value, "en")
            self.assertFalse(language.expanded)
            approve = settings.query_one("#approve-for-me", Switch)
            self.assertTrue(approve.value)
            await pilot.press("enter")
            self.assertFalse(approve.value)
            await pilot.press("up", "enter", "down")
            self.assertTrue(language.expanded)
            self.assertEqual(language.value, "en")
            await pilot.press("escape")
            self.assertIs(app.screen, settings)
            self.assertFalse(language.expanded)
            self.assertTrue(language.has_focus)
            self.assertEqual(language.value, "en")
            await pilot.press("enter", "down", "enter")
            self.assertEqual(language.value, "zh")
            self.assertTrue(language.has_focus)
            await pilot.press("down", "down", "enter")
            theme = settings.query_one("#ui-theme", Select)
            self.assertTrue(theme.expanded)
            await pilot.press("down", "enter")
            self.assertEqual(theme.value, "paper")
            await pilot.press("down", "down", "down", "down")
            directory = settings.query_one(DirectoryInput)
            self.assertTrue(directory.has_focus)
            await pilot.press("x")
            self.assertEqual(directory.value, "")
            await pilot.press("enter", "x", "escape")
            self.assertIs(app.screen, settings)
            self.assertEqual(directory.value, "")
            await pilot.press("escape")
            self.assertIs(app.screen, app.main_screen)
            self.assertEqual(app.view_preferences.get("ui_theme", "gray"), "gray")

    async def test_typing_selected_composer_inserts_first_character_without_sending(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await pilot.press("enter")
            await pilot.pause(.2)
            composer = app.query_one(Composer)
            composer.load_text("draft ")
            composer.move_cursor(composer.document.end)
            await pilot.press("escape", "end", "down")
            self.assertTrue(composer.has_focus)
            self.assertTrue(composer.read_only)
            await pilot.press("h", "i")
            self.assertFalse(composer.read_only)
            self.assertEqual(composer.text, "draft hi")
            self.assertFalse(any(m == "turn/start" for m, _ in app.client.calls))

    async def test_mouse_selection_copies_formatted_unicode_text_in_demo(self):
        app = self.app()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause(.2)
            await pilot.press("ctrl+l", "enter")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.ingest({"id": "copy-test", "type": "agentMessage", "text": "**你好 hello**\n\n`code example`"})
            app.paint(force=True)
            await pilot.pause(.2)
            transcript = app.query_one("#transcript")
            self.assertIsInstance(transcript.render(), Content)
            # Drag across the rendered Markdown, not a synthetic selection object.
            await pilot.mouse_down(transcript, offset=(0, 1))
            await pilot.hover(transcript, offset=(10, 1))
            await pilot.mouse_up(transcript, offset=(10, 1))
            await pilot.pause(.2)
            self.writer.assert_not_called()
            self.assertEqual(app.screen.get_selected_text(), "你好 hello")
            self.writer.reset_mock()
            await pilot.press("ctrl+c")
            await pilot.pause(.1)
            self.writer.assert_called_with("你好 hello")
            self.assertFalse(any(m == "turn/interrupt" for m, _ in app.client.calls))
            self.writer.reset_mock()
            key = list(XTermParser().feed("\x1b[99;9u"))[0].key
            self.assertEqual(key, "super+c")
            await pilot.press(key)
            await pilot.pause(.1)
            self.writer.assert_called_once_with("你好 hello")
            await pilot.resize_terminal(60, 24)
            await pilot.pause(.2)
            self.assertIsInstance(transcript.render(), Content)

    async def test_copy_failure_is_not_reported_as_success(self):
        app = self.app()
        self.writer.return_value = False
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            with patch.object(app, "notify") as notify:
                app.copy_to_clipboard("synthetic copy check")
                await pilot.pause(.2)
                self.writer.assert_called_with("synthetic copy check")
                self.assertIn("unavailable", notify.call_args.args[0])
                self.assertEqual(notify.call_args.kwargs["severity"], "warning")

    async def test_copy_removes_fill_but_keeps_code_and_table_spacing(self) -> None:
        """Remove display padding across widths. 不同宽度下排除显示边距。"""
        app = self.app()
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(.2)
            await pilot.press("ctrl+l", "enter")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.items.clear()
            session.ingest({"id": "copy-padding", "type": "agentMessage", "text":
                "你好  hello\n\nSecond paragraph\n\n"
                "```python\ndef greet():\n    value = 'a  b'\n    return value\n```\n\n"
                "| Name | Value |\n|---|---|\n| foo | bar |"})
            app.paint(force=True)
            await pilot.pause(.2)
            transcript = app.query_one("#transcript")
            for width in (100, 60):
                await pilot.resize_terminal(width, 40)
                await pilot.pause(.2)
                rendered = transcript.render().plain
                self.assertTrue(any(line.endswith("   ") for line in rendered.splitlines()))
                app.screen.selections = {transcript: Selection(None, None)}
                await pilot.press("ctrl+c")
                await pilot.pause(.1)
                copied = self.writer.call_args.args[0]
                self.assertTrue(all(line == line.rstrip(" \t") for line in copied.splitlines()))
                self.assertIn("你好  hello\n\nSecond paragraph", copied)
                self.assertIn("def greet():\n    value = 'a  b'\n    return value", copied)
                self.assertIn("Name  Value", copied)
                # Copy only source indentation, without trimming it.
                # 仅选中代码原始缩进时，四格空格仍完整保留。
                row = next(i for i, line in enumerate(rendered.splitlines()) if "value =" in line)
                selected = transcript.get_selection(Selection(Offset(1, row), Offset(5, row)))
                self.assertEqual(selected[0], "    ")

    async def test_multiline_mouse_copy_and_intentional_draft_spaces(self) -> None:
        """Exercise mouse selection and untouched drafts. 验证鼠标选区及草稿空格。"""
        app = self.app()
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(.2)
            await pilot.press("ctrl+l", "enter")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.items.clear()
            session.ingest({"id": "copy-lines", "type": "agentMessage", "text":
                "First paragraph\n\nSecond paragraph"})
            app.paint(force=True)
            await pilot.pause(.2)
            transcript = app.query_one("#transcript")
            await pilot.mouse_down(transcript, offset=(0, 1))
            await pilot.hover(transcript, offset=(70, 3))
            await pilot.mouse_up(transcript, offset=(70, 3))
            expected = "First paragraph\n\nSecond paragraph"
            self.assertEqual(app.screen.get_selected_text(), expected)
            await pilot.press("ctrl+c")
            await pilot.pause(.1)
            self.writer.assert_called_with(expected)
            app.screen.clear_selection()
            composer = app.query_one(Composer)
            composer.load_text("  draft  ")
            app.focus_composer(edit=True)
            composer.action_select_all()
            await pilot.press("ctrl+c")
            await pilot.pause(.1)
            self.writer.assert_called_with("  draft  ")

    async def test_drag_keeps_wrapped_layout_and_unicode_selection_stable(self) -> None:
        """Focus must not reflow text under the mouse. 焦点变化不重排鼠标下的文字。"""
        app = self.app()
        async with app.run_test(size=(90, 36)) as pilot:
            await pilot.pause(.2)
            await pilot.press("ctrl+l", "enter")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.items.clear()
            session.ingest({"id": "wrapped-copy", "type": "agentMessage",
                            "text": "Mixed 中文 wrapping text " * 15 + "\n\n你好 hello END"})
            app.paint(force=True)
            await pilot.pause(.2)
            transcript = app.query_one("#transcript")
            scroll = app.query_one("#transcript-scroll")
            for width in (90, 60):
                app.screen.clear_selection()
                app.focus_composer(edit=True)
                await pilot.resize_terminal(width, 36)
                scroll.scroll_home(animate=False, immediate=True)
                await pilot.pause(.2)
                before = transcript.render().plain
                before_width = transcript.content_size.width
                row = next(i for i, line in enumerate(before.splitlines())
                           if "你好 hello END" in line)
                await pilot.mouse_down(transcript, offset=(0, row))
                await pilot.hover(transcript, offset=(10, row))
                await pilot.mouse_up(transcript, offset=(10, row))
                await pilot.pause(.1)
                self.assertEqual(transcript.content_size.width, before_width)
                self.assertEqual(transcript.render().plain, before)
                self.assertEqual(app.screen.get_selected_text(), "你好 hello")
                await pilot.press("ctrl+c")
                await pilot.pause(.1)
                self.writer.assert_called_with("你好 hello")

    async def test_upward_drag_stops_at_visible_transcript_edge(self) -> None:
        """Dragging into the title must not copy controls below the cursor.

        拖入标题栏时选区停在日志顶部，不复制其他界面控件。
        """
        app = self.app()
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause(.2)
            await pilot.press("ctrl+l", "enter")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.items.clear()
            session.ingest({
                "id": "upward-copy",
                "type": "agentMessage",
                "text": "\n\n".join(f"ROW {row:02d} text" for row in range(50)),
            })
            app.paint(force=True)
            await pilot.pause(.2)
            transcript = app.query_one("#transcript")
            scroll = app.query_one("#transcript-scroll")
            scroll.scroll_end(animate=False, immediate=True)
            await pilot.pause(.2)
            start_y = scroll.region.bottom - transcript.region.y - 3
            await pilot.mouse_down(transcript, offset=(8, start_y))
            await pilot.hover(offset=(15, 1))
            await pilot.mouse_up(offset=(15, 1))
            await pilot.pause(.1)
            selected = app.screen.get_selected_text()
            self.assertIsNotNone(selected)
            self.assertIn("ROW 41", selected)
            self.assertNotIn("AtomX / CODEX", selected)
            self.assertNotIn("新会话", selected)
            self.assertEqual(set(app.screen.selections), {transcript})
            await pilot.press("ctrl+c")
            await pilot.pause(.1)
            self.writer.assert_called_with(selected)
