"""Compatibility import for pointer support. 鼠标指针旧路径兼容入口。"""

import sys

from .terminal import mouse_pointer as _module
from .terminal.mouse_pointer import *  # noqa: F403

sys.modules[__name__] = _module
