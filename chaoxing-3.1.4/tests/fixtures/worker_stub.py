"""Offline subprocess fixture. Never imports or connects to a course platform."""

import json
import os
import sys
import threading
import time

from backend.worker import watch_parent

payload = json.loads(sys.stdin.buffer.readline())
threading.Thread(target=watch_parent, args=(sys.stdin.buffer,), daemon=True).start()
hold = os.getenv("OFFLINE_FIXTURE_HOLD") == "1"
started = time.monotonic()
while hold or time.monotonic() - started < 0.4:
    print(json.dumps({"event": "phase", "data": {"phase": "offline_fixture"}}), flush=True)
    time.sleep(0.05)
print(json.dumps({
    "event": "result", "data": {"outcome": "succeeded", "completed": 1, "total": 1},
}), flush=True)
