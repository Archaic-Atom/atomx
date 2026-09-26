"""Compatibility import for backend actions. 后端行为旧路径兼容入口。"""

import sys

from .actions import backend_actions as _module
from .actions.backend_actions import *  # noqa: F403

sys.modules[__name__] = _module
