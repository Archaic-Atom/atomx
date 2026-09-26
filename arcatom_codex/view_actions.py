"""Compatibility import for view actions. 界面行为旧路径兼容入口。"""

import sys

from .actions import view_actions as _module
from .actions.view_actions import *  # noqa: F403

sys.modules[__name__] = _module
