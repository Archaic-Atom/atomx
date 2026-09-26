"""Compatibility import for the Codex transport. Codex 传输旧路径兼容入口。"""

import sys

from .backend import rpc as _module
from .backend.rpc import *  # noqa: F403

sys.modules[__name__] = _module
