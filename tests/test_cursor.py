"""Verify cursor contrast and blinking in terminal themes. 验证终端主题光标。"""

import tempfile
import unittest

from arcatom_codex.backend.demo import DemoClient
from arcatom_codex.ui import ArcatomApp
from arcatom_codex.widgets import Composer


class CursorTests(unittest.IsolatedAsyncioTestCase):
    """Keep editing cursors visible across themes. 保证各主题中的编辑光标可见。"""

    async def test_input_cursors_remain_visible_and_blink(self) -> None:
        """Check cursor contrast and blinking in dark and light themes.

        检查深浅主题中的光标对比度与闪烁。
        """
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(100, 40)) as pilot:
            for theme, foreground in (
                ("gray", "#202020"),
                ("paper", "#ffffff"),
            ):
                app.apply_appearance({"ui_theme": theme, "accent": "violet"})
                await app.open_session("demo-login")
                await pilot.pause(0.1)
                composer = app.query_one(Composer)
                composer.render_lines(composer.size.region)
                cursor_style = composer._theme.cursor_style
                self.assertEqual(cursor_style.bgcolor.name, app.palette.accent)
                self.assertEqual(cursor_style.color.name, foreground)
                self.assertFalse(cursor_style.reverse)
                self.assertTrue(composer._cursor_visible)
                composer._toggle_cursor_blink_visible()
                self.assertFalse(composer._cursor_visible)
                composer._toggle_cursor_blink_visible()
                self.assertTrue(composer._cursor_visible)

                app.show_home()
                search = app.query_one("#search")
                search.focus()
                await pilot.pause(0.1)
                search_style = search.get_component_rich_style("input--cursor")
                self.assertEqual(search_style.bgcolor.name, app.palette.accent)
                self.assertEqual(search_style.color.name, foreground)
                self.assertFalse(search_style.reverse)
