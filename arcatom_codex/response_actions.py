"""Compatibility import for response actions. 回复行为旧路径兼容入口。"""

import sys

from .actions import response_actions as _module
from .actions.response_actions import *  # noqa: F403

sys.modules[__name__] = _module
