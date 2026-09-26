"""Compatibility import for the session model. 会话模型旧路径兼容入口。"""

import sys

from .core import state as _module
from .core.state import *  # noqa: F403

sys.modules[__name__] = _module
