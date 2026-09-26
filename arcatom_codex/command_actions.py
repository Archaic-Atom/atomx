"""Compatibility import for command actions. 命令行为旧路径兼容入口。"""

import sys

from .actions import command_actions as _module
from .actions.command_actions import *  # noqa: F403

sys.modules[__name__] = _module
