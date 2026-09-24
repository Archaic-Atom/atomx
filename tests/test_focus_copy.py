"""User-visible focus, nested settings, typing and mouse-copy regressions."""
import tempfile
import unittest
from unittest.mock import patch

from textual.content import Content
from textual._xterm_parser import XTermParser
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

    async def test_home_arrows_return_to_only_input_focus_and_enter_creates(self):
        app = self.app()
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            search, sessions = app.query_one("#search"), app.query_one("#sessions")
            self.assertTrue(search.has_focus)
            self.assertIsNone(sessions.highlighted)
            self.assertEqual(app.palette.background, "#202020")
            self.assertEqual(app.palette.accent, "#e5a277")
            self.assertGreater(search.region.y, sessions.region.y)
            self.assertEqual(search.region.height, 5)
            await pilot.press("up")
            self.assertTrue(sessions.has_focus)
            self.assertEqual(sessions.highlighted, sessions.selectable_indices()[-1])
            app.paint(force=True)
            await pilot.press("down")
            self.assertTrue(search.has_focus)
            self.assertIsNone(sessions.highlighted)
            await pilot.press("down")
            self.assertEqual(app.screen.focused.id, "new-session")
            await pilot.press("down")
            self.assertEqual(app.screen.focused.id, "settings")
            await pilot.press("down")
            self.assertTrue(sessions.has_focus)
            self.assertEqual(sessions.get_option_at_index(sessions.highlighted).id, "demo-login")
            await pilot.press("up", "up", "up", "enter")
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
            await pilot.press("enter")
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
