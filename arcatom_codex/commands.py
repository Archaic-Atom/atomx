"""Compatibility import for command definitions. 命令定义旧路径兼容入口。"""

import sys

from .core import commands as _module
from .core.commands import *  # noqa: F403

sys.modules[__name__] = _module
