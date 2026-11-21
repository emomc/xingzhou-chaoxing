"""Optional, best-effort progress reporting for headless workers."""

import threading

_lock = threading.RLock()
_callback = None


def set_progress_callback(callback):
    global _callback
    with _lock:
        _callback = callback


def report(event, **fields):
    with _lock:
        callback = _callback
    if callback is not None:
        try:
            callback(event, fields)
        except Exception:
            # Progress transport must not change the outcome of a platform request.
            pass
