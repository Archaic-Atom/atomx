"""Clickable transcript links without accidental opens during text selection."""

import tempfile
import unittest
from unittest.mock import patch

from textual import events
from rich.console import Console

from arcatom_codex.appearance import PALETTES
from arcatom_codex.demo import DemoClient
from arcatom_codex.rendering import pretty
from arcatom_codex.ui import ArcatomApp


class TranscriptLinkTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real rendered link cells and mouse events. 验证真实链接单元和鼠标事件。"""

    def test_link_uses_rose_on_dark_and_light_themes(self) -> None:
        """Preserve clickable metadata with readable rose. 链接变粉且仍可点击。"""
        url = "https://cvpr.thecvf.com"
        console = Console(width=80, force_terminal=True, color_system="truecolor")
        for theme, color in (("gray", "#e8a0b1"), ("paper", "#993f58")):
            segments = list(console.render(pretty(
                {"type": "agentMessage", "text": f"[CVPR]({url})"},
                palette=PALETTES[theme],
            )))
            link = next(segment for segment in segments if "CVPR" in segment.text)
            self.assertEqual(link.style.link, url)
            self.assertEqual(link.style.color.name, color)

    @staticmethod
    def link_cell(app: ArcatomApp, url: str) -> tuple[int, int]:
        """Find a visible cell for one rendered URL. 找到目标链接的可见单元。"""
        for y in range(app.size.height):
            for x in range(app.size.width):
                if app.screen.get_style_at(x, y).link == url:
                    return x, y
        raise AssertionError(f"Link was not rendered: {url}")

    async def test_click_opens_http_link_but_drag_does_not(self) -> None:
        """Distinguish a click from a selection within one widget. 区分单击与拖选。"""
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        url = "https://example.com/path"
        http_url = "http://example.org/plain"
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(0.2)
            await app.open_session("demo-login")
            await pilot.pause(0.2)
            session = app.store.get("demo-login")
            session.items.clear()
            session.ingest(
                {
                    "id": "web-link",
                    "type": "agentMessage",
                    "text": (
                        f"See [website]({url}) and [HTTP]({http_url}); "
                        "copy this text."
                    ),
                }
            )
            app.paint(force=True)
            await pilot.pause(0.1)
            x, y = self.link_cell(app, url)
            with patch(
                "arcatom_codex.widgets.webbrowser.open", return_value=True
            ) as opener:
                await pilot.click(offset=(x, y))
                await pilot.pause(0.1)
                opener.assert_called_once_with(url)
                await pilot.click(offset=self.link_cell(app, http_url))
                await pilot.pause(0.1)
                self.assertEqual(opener.call_args_list[-1].args, (http_url,))
                self.assertEqual(opener.call_count, 2)
                opener.reset_mock()
                await pilot.mouse_down(offset=(x, y))
                await pilot.hover(offset=(x + 2, y))
                await pilot.mouse_up(offset=(x + 2, y))
                await pilot.pause(0.1)
                self.assertTrue(app.screen.get_selected_text())
                # Real terminals synthesize a Click after same-widget mouse up;
                # Textual's pilot sends only the requested mouse events.
                # 真实终端会在同一控件内松开后补发 Click，pilot 不会。
                transcript = app.query_one("#transcript")
                await transcript.on_click(
                    events.Click(
                        transcript,
                        x + 2,
                        y,
                        0,
                        0,
                        1,
                        False,
                        False,
                        False,
                        screen_x=x + 2,
                        screen_y=y,
                        style=app.screen.get_style_at(x + 2, y),
                    )
                )
                opener.assert_not_called()

    async def test_non_web_link_does_not_open(self) -> None:
        """Reject non-HTTP schemes and plain text. 拒绝非网页协议与普通文字。"""
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        url = "mailto:person@example.com"
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(0.2)
            await app.open_session("demo-login")
            await pilot.pause(0.2)
            session = app.store.get("demo-login")
            session.items.clear()
            session.ingest(
                {
                    "id": "local-link",
                    "type": "agentMessage",
                    "text": f"[local]({url}) plain text",
                }
            )
            app.paint(force=True)
            await pilot.pause(0.1)
            x, y = self.link_cell(app, url)
            with patch(
                "arcatom_codex.widgets.webbrowser.open", return_value=True
            ) as opener:
                await pilot.click(offset=(x, y))
                await pilot.click(offset=(x + 8, y))
                await pilot.pause(0.1)
                opener.assert_not_called()
