"""Keyboard protocol ownership and desktop shortcuts. 按键协议与系统快捷键回归。"""

import inspect
import sys
import tempfile
import unittest
from unittest.mock import patch

from textual import events
from textual._xterm_parser import XTermParser
from textual.driver import Driver

from arcatom_codex.appearance import ROBOT, ROBOT_COMPACT
from arcatom_codex.demo import DemoClient
from arcatom_codex.keyboard import (
    ArrowGesture,
    keyboard_driver,
    normalize_keypad_key,
    reserved_navigation,
)
from arcatom_codex.ui import AtomXApp, Composer


class KeyboardPolicyTests(unittest.TestCase):
    def test_robot_edges_use_one_cell_ascii_in_both_layouts(self):
        for rows in (ROBOT, ROBOT_COMPACT):
            self.assertEqual({len(row) for row in rows}, {11})
            self.assertTrue(all(row.isascii() for row in rows))
            self.assertTrue(all(row[9] in "+|" for row in rows if row.strip() != "o"))

    def test_standard_protocol_does_not_pop_the_parent_stack(self):
        # Use the actual platform driver, including Windows in the CI matrix.
        base = AtomXApp.get_driver_class(None)
        standard = keyboard_driver(base, enhanced=False)
        instance = object.__new__(standard)
        with patch.object(base, "write") as write:
            for _ in range(3):  # Startup, native handoff resume and another resume.
                instance.write("\x1b[>1u")
                instance.write("\x1b[>25u")
                instance.write("\x1b[?1049h")
                instance.write("ordinary output")
                instance.write("\x1b[<u")
            self.assertEqual(write.call_count, 6)
            self.assertNotIn("\x1b[<u", [call.args[0] for call in write.call_args_list])
        self.assertIs(keyboard_driver(base, enhanced=True), base)
        # Fail visibly if a Textual upgrade changes the driver contract.
        self.assertIn(r'\x1b[>', inspect.getsource(base.start_application_mode))
        self.assertIn(r'\x1b[<u', inspect.getsource(base.stop_application_mode))

    def test_system_navigation_policy_and_normal_editing_keys(self):
        for platform in ("darwin", "linux", "win32"):
            for arrow in ("left", "right", "up", "down"):
                for prefix in ("super+", "ctrl+alt+", "ctrl+super+"):
                    self.assertTrue(reserved_navigation(prefix + arrow, platform))
                self.assertFalse(reserved_navigation(arrow, platform))
                self.assertFalse(reserved_navigation("shift+" + arrow, platform))
                self.assertEqual(reserved_navigation("ctrl+" + arrow, platform),
                                 platform == "darwin")
            self.assertFalse(reserved_navigation("super+c", platform))

    def test_double_arrows_use_input_timestamps_and_consume_pairs(self):
        gesture = ArrowGesture()
        self.assertFalse(gesture.press("up", 1.0))
        self.assertTrue(gesture.press("up", 1.1))
        self.assertFalse(gesture.press("up", 1.2))
        self.assertFalse(gesture.press("up", 1.6))
        self.assertFalse(gesture.press("down", 1.7))
        gesture.reset()
        self.assertFalse(gesture.press("down", 1.8))

    def test_keypad_decimal_protocols_and_shortcuts(self) -> None:
        """Normalize decimal without rewriting shortcuts or Delete.

        转换小数点，不改写组合键及关闭 Num Lock 后的 Delete。
        """
        for sequence in (".", "\x1bOn", "\x1b[57409u", "\x1b[57409;129u"):
            event = next(iter(XTermParser().feed(sequence)))
            normalized = normalize_keypad_key(event)
            self.assertEqual((normalized.key, normalized.character), ("full_stop", "."))
            self.assertTrue(normalized.is_printable)
        for sequence in ("\x1b[57409;5u", "\x1b[57426u", "\x1b[3~"):
            event = next(iter(XTermParser().feed(sequence)))
            self.assertIs(normalize_keypad_key(event), event)

    def test_parser_retains_modifiers_and_distinguishes_newline(self):
        parser = XTermParser(debug=False)
        keys = [message.key for message in parser.feed(
            "\x1b[1;5D\x1b[1;5C\x1b[13;2u\n")
            if isinstance(message, events.Key)]
        self.assertEqual(keys, ["ctrl+left", "ctrl+right", "shift+enter", "ctrl+j"])


class KeyboardUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_reserved_arrow_does_not_move_cursor_or_change_focus(self):
        app = AtomXApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await pilot.press("enter")
            composer = app.query_one(Composer)
            composer.load_text("preserve my draft")
            composer.move_cursor((0, 8))
            with patch("arcatom_codex.ui.reserved_navigation",
                       side_effect=lambda key: reserved_navigation(key, "darwin")):
                await pilot.press("ctrl+left", "ctrl+right", "ctrl+up", "ctrl+down")
            self.assertEqual(composer.cursor_location, (0, 8))
            self.assertTrue(composer.has_focus)
            await pilot.press("ctrl+j")
            self.assertEqual(composer.text, "preserve\n my draft")
            await pilot.press("f2")
            focused = app.screen.focused
            await pilot.press("super+right", "ctrl+alt+left")
            self.assertIs(app.screen.focused, focused)


    async def test_keypad_decimal_types_in_home_and_composer(self) -> None:
        """Send decoded terminal input through real application routing.

        将终端协议解析结果送入真实路由，验证首页及输入框都能输入小数点。
        """
        app = AtomXApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            app.post_message(next(iter(XTermParser().feed("\x1b[57409u"))))
            await pilot.pause(.1)
            self.assertEqual(app.query_one("#search").value, ".")
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            composer = app.query_one(Composer)
            composer.load_text("1")
            composer.move_cursor(composer.document.end)
            app.leave_composer()
            app.post_message(next(iter(XTermParser().feed("\x1b[57409;129u"))))
            await pilot.pause(.1)
            self.assertTrue(composer.has_focus)
            self.assertEqual(composer.text, "1.")
            for sequence in (".", "\x1bOn", "\x1b[57409u"):
                app.post_message(next(iter(XTermParser().feed(sequence))))
                await pilot.pause(.05)
            self.assertEqual(composer.text, "1....")
            app.post_message(next(iter(XTermParser().feed("\x1b[57409;5u"))))
            await pilot.pause(.1)
            self.assertEqual(composer.text, "1....")
