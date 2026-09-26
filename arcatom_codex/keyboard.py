"""Compatibility import for terminal keyboard. 终端键盘旧路径兼容入口。"""

import sys

from .terminal import keyboard as _module
from .terminal.keyboard import *  # noqa: F403

sys.modules[__name__] = _module
