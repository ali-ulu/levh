"""Suite-wide environment guards.

A test must report on the code, not on the machine it runs on. The librarian
watchdog starts with the app and writes its own findings into whatever store
the app is using, which is a memory no test asked for — and, being a
background thread, one that arrives at an unpredictable moment. Tests that
exercise the watchdog turn it on themselves.
"""

import os

os.environ["LEVH_LIBRARIAN"] = "0"
