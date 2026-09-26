"""Compatibility import for home actions. 首页行为旧路径兼容入口。"""

import sys

from .actions import home_actions as _module
from .actions.home_actions import *  # noqa: F403

sys.modules[__name__] = _module
