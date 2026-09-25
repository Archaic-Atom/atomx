"""Typed access to the owning workspace. 控件与行为模块访问工作台的类型边界。"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.widget import Widget

if TYPE_CHECKING:
    from .ui import AtomXApp


class WorkspaceAccess:
    """Expose the workspace to its child widgets. 子控件访问所属工作台。"""

    @property
    def workspace(self) -> AtomXApp:
        """Return the app hosting this widget. 获取控件所在的应用。"""
        return cast("AtomXApp", cast(Widget, self).app)


class AppActions:
    """Type the host for application behavior mixins. 声明行为模块的宿主类型。"""

    @property
    def workspace(self) -> AtomXApp:
        """Return the application extended by this mixin. 获取当前宿主应用。"""
        return cast("AtomXApp", self)
