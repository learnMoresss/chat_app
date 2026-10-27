from __future__ import annotations

import threading
import time
from typing import Optional

from .lifecycle import reap_idle_instances

_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def start_reaper(*, interval_seconds: float = 60.0) -> None:
    """后台线程：按 idle TTL 回收空闲本地插件实例。"""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def _loop() -> None:
        while not _stop.wait(interval_seconds):
            try:
                reap_idle_instances()
            except Exception:
                pass

    _thread = threading.Thread(target=_loop, name="plugin-reaper", daemon=True)
    _thread.start()


def stop_reaper() -> None:
    _stop.set()
