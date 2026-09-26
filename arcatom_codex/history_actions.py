"""Compatibility import for history actions. 历史行为旧路径兼容入口。"""

import sys

from .actions import history_actions as _module
from .actions.history_actions import *  # noqa: F403

sys.modules[__name__] = _module
