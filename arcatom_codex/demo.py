"""Compatibility import for demo sessions. 演示后端旧路径兼容入口。"""

import sys

from .backend import demo as _module
from .backend.demo import *  # noqa: F403

sys.modules[__name__] = _module
