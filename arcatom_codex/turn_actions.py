"""Compatibility import for turn actions. 回合行为旧路径兼容入口。"""

import sys

from .actions import turn_actions as _module
from .actions.turn_actions import *  # noqa: F403

sys.modules[__name__] = _module
