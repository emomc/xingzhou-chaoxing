"""Offline course discovery fixture. Makes no network requests."""

import json
import os
import sys
import threading
import time

from backend.worker import watch_parent

json.loads(sys.stdin.buffer.readline())
threading.Thread(target=watch_parent, args=(sys.stdin.buffer,), daemon=True).start()
while os.getenv("OFFLINE_FIXTURE_HOLD") == "1":
    time.sleep(0.05)
print(json.dumps({
    "state": "done",
    "courses": [
        {"id": "101", "title": "Offline course one", "teacher": "Offline teacher"},
        {"id": "202", "title": "Offline course two", "teacher": ""},
    ],
}), flush=True)
