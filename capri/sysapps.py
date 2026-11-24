"""Platform switch for app discovery, matching, and launching."""
from __future__ import annotations

import sys

if sys.platform == "darwin":
    from .macapps import *  # noqa: F401, F403
else:
    from .apps import *  # noqa: F401, F403
