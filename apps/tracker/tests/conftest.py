import os

# Settings() runs at import time and requires this; tests that verify battle tokens patch the real key in.
os.environ.setdefault("TRACKER_ARENA_SERVICE_JWT_PUBLIC_KEY", "dGVzdA==")
