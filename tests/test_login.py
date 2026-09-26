"""Exercise the Codex-managed sign-in UI without changing real credentials.

用内存后端验证登录界面，不触碰真实凭据。
"""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from textual.widgets import Button, Static

from arcatom_codex.demo import DemoClient
from arcatom_codex.dialogs import Login
from arcatom_codex.ui import AtomXApp


class AuthClient(DemoClient):
    """Simulate an account that completes the official OAuth flow.

    模拟通过官方 OAuth 登录的账户。
    """

    def __init__(self, logged_in: bool = False) -> None:
        """Initialize the simulated auth state. 初始化模拟登录状态。"""
        super().__init__()
        self.logged_in = logged_in

    async def call(
        self, method: str, params: dict | None = None, timeout: float = 30
    ) -> dict:
        """Return only fake auth responses. 仅返回模拟的认证响应。"""
        if method.startswith("account/login/") or method == "account/read":
            self.calls.append((method, params or {}))
            if method == "account/read":
                return {
                    "requiresOpenaiAuth": True,
                    "account": {"type": "chatgpt"} if self.logged_in else None,
                }
            if method == "account/login/start":
                if (params or {}).get("type") == "chatgptDeviceCode":
                    return {
                        "type": "chatgptDeviceCode", "loginId": "login-1",
                        "verificationUrl": "https://example.com/device",
                        "userCode": "ABCD-1234",
                    }
                return {
                    "type": "chatgpt", "loginId": "login-1",
                    "authUrl": "https://example.com/sign-in",
                }
            return {"status": "canceled"}
        return await super().call(method, params, timeout)


class LoginTests(unittest.IsolatedAsyncioTestCase):
    """Check detection, completion, and a deferred login. 验证发现、完成和稍后登录。"""

    async def test_unauthenticated_account_can_sign_in(self) -> None:
        """Prompt once and continue after the matching completion event.

        未登录时提示，并在对应登录完成事件后继续。
        """
        client = AuthClient()
        app = AtomXApp(tempfile.gettempdir(), client=client)
        with patch("arcatom_codex.dialogs.webbrowser.open", return_value=True) as browser:
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause(0.3)
                self.assertIsInstance(app.screen, Login)
                self.assertTrue(app.auth_needed)
                self.assertTrue(app.query_one("#login", Button).display)
                app.screen.query_one("#login-chatgpt", Button).press()
                await pilot.pause(0.2)
                self.assertIn(
                    ("account/login/start", {"type": "chatgpt"}), client.calls
                )
                browser.assert_called_once_with("https://example.com/sign-in")
                client.logged_in = True
                await client.events.put({
                    "method": "account/login/completed",
                    "params": {"loginId": "other", "success": True},
                })
                await pilot.pause(0.1)
                self.assertIsInstance(app.screen, Login)
                await client.events.put({
                    "method": "account/login/completed",
                    "params": {"loginId": "login-1", "success": True},
                })
                await pilot.pause(0.3)
                self.assertIs(app.screen, app.main_screen)
                self.assertFalse(app.auth_needed)
                self.assertFalse(app.query_one("#login", Button).display)

    async def test_deferred_device_code_login_can_be_reopened(self) -> None:
        """Keep a way back and expose the verification code.

        稍后登录仍可重开，并显示设备验证码。
        """
        client = AuthClient()
        app = AtomXApp(tempfile.gettempdir(), client=client)
        with patch("arcatom_codex.dialogs.webbrowser.open", return_value=True):
            async with app.run_test(size=(100, 34)) as pilot:
                await pilot.pause(0.3)
                await pilot.press("escape")
                await pilot.pause(0.1)
                self.assertIs(app.screen, app.main_screen)
                await app.home_command("/login")
                await pilot.pause(0.1)
                self.assertIsInstance(app.screen, Login)
                self.assertFalse(
                    any(method == "thread/start" for method, _ in client.calls)
                )
                app.screen.query_one("#login-device", Button).press()
                await pilot.pause(0.2)
                self.assertIn(
                    "ABCD-1234",
                    app.screen.query_one("#login-code", Static).render().plain,
                )
                await pilot.press("escape")
                await pilot.pause(0.1)
                self.assertIn(
                    ("account/login/cancel", {"loginId": "login-1"}), client.calls
                )
                app.query_one("#login", Button).press()
                await pilot.pause(0.1)
                self.assertIsInstance(app.screen, Login)

    async def test_existing_account_is_not_prompted(self) -> None:
        """Leave signed-in users on the home screen. 已登录用户正常进入首页。"""
        app = AtomXApp(tempfile.gettempdir(), client=AuthClient(True))
        async with app.run_test(size=(100, 34)) as pilot:
            await pilot.pause(0.3)
            self.assertIs(app.screen, app.main_screen)
            self.assertFalse(app.auth_needed)
