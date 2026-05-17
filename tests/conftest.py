"""Shared pytest fixtures + early env vars.

`agent.api.conversations` mints a process-local random session secret at
import time when ``AGENT_SESSION_SECRET`` is empty — the warning is
correct in prod but breaks deterministic tests. Set a stable value here
BEFORE any agent.* module is imported by a test.
"""

from __future__ import annotations

import os

os.environ.setdefault("AGENT_SESSION_SECRET", "test-secret-deterministic")
os.environ.setdefault("AGENT_CLEANUP_INTERVAL_SECONDS", "0")
os.environ.setdefault("AGENT_PERSIST_CONVERSATIONS", "false")
