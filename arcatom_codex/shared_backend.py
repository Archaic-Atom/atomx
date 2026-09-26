"""Compatibility import for shared backend. 共享后端旧路径兼容入口。"""

import sys

from .backend import shared_backend as _module
from .backend.shared_backend import *  # noqa: F403

sys.modules[__name__] = _module
