"""Platform switch for system, window, media, and volume controls."""
from __future__ import annotations

import sys

if sys.platform == "darwin":
    from .macctl import *  # noqa: F401, F403
else:
    from .winctl import *  # noqa: F401, F403
