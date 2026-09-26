"""Mouse pointer feedback on selectable transcript cells. 日志鼠标形状回归。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from arcatom_codex.demo import DemoClient
from arcatom_codex.mouse_pointer import pointer_driver
from arcatom_codex.ui import ArcatomApp, Composer


class MousePointerTests(unittest.IsolatedAsyncioTestCase):
    """Exercise iTerm2 pointer changes through real mouse events. 验证真实悬停事件。"""

    def test_driver_restores_terminal_pointer(self) -> None:
        """Keep OSC 22 scoped to app mode. 仅在应用运行期间接管鼠标形状。"""
        class FakeDriver:
            def __init__(self) -> None:
                self.writes: list[str] = []

            def start_application_mode(self) -> None:
                pass

            def stop_application_mode(self) -> None:
                pass

            def write(self, value: str) -> None:
                self.writes.append(value)

            def flush(self) -> None:
                pass

        driver = pointer_driver(FakeDriver, "iTerm.app")()
        driver.start_application_mode()
        self.assertEqual(driver.writes, ["\x1b]22;arrow\x1b\\"])
        driver.stop_application_mode()
        self.assertEqual(driver.writes[-1], "\x1b]22;xterm\x1b\\")

    async def test_hover_link_activity_text_and_plain_area(self) -> None:
        """Use hand on actions, I-beam on edit and selection, arrow elsewhere.

        链接和活动用手形，编辑与拖选用 I 形，其余位置用箭头。
        """
        with patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}):
            app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            session = app.store.get("demo-dashboard")
            session.items.clear()
            session.ingest({
                "id": "reply", "type": "agentMessage",
                "text": "Plain text and [website](https://example.com).",
            })
            session.ingest({
                "id": "search", "type": "webSearch", "query": "AtomX",
            })
            app.paint(force=True)
            await pilot.pause(.1)
            driver = app._driver
            driver._atomx_pointer_active = True
            driver._atomx_pointer_shape = "default"
            self.assertEqual(driver._atomx_pointer_shape, "default")
            app._set_pointer_shape("pointer")
            self.assertEqual(driver._atomx_pointer_shape, "pointer")
            app._set_pointer_shape("default")
            link_cell = next(
                (x, y)
                for y in range(app.size.height)
                for x in range(app.size.width)
                if app.screen.get_style_at(x, y).link == "https://example.com"
            )
            activity_cell = next(
                (x, y)
                for y in range(app.size.height)
                for x in range(app.size.width)
                if app.screen.get_style_at(x, y).meta.get("atomx_activity_id")
                == "search"
            )
            await pilot.hover(offset=link_cell)
            self.assertEqual(driver._atomx_pointer_shape, "pointer")
            await pilot.hover(offset=activity_cell)
            self.assertEqual(driver._atomx_pointer_shape, "pointer")
            composer = app.query_one(Composer)
            transcript = app.query_one("#transcript")
            self.assertEqual(composer.styles.pointer, "text")
            self.assertEqual(transcript.styles.pointer, "default")
            await pilot.hover(offset=(composer.region.x + 2, composer.region.y + 1))
            self.assertEqual(driver._atomx_pointer_shape, "text")
            await pilot.hover(offset=(0, 0))
            self.assertEqual(driver._atomx_pointer_shape, "default")
            plain_cell = (transcript.region.x + 2, transcript.region.y + 1)
            await pilot.hover(offset=plain_cell)
            self.assertEqual(driver._atomx_pointer_shape, "default")
            await pilot.mouse_down(offset=plain_cell)
            self.assertEqual(driver._atomx_pointer_shape, "text")
            await pilot.mouse_up(offset=plain_cell)
            self.assertEqual(driver._atomx_pointer_shape, "default")
