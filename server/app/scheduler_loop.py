"""后台轮询到期 schedules。"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

from . import schedules as sched

_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def start_scheduler(*, interval_seconds: float = 30.0) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def _loop() -> None:
        while not _stop.wait(interval_seconds):
            try:
                _tick()
            except Exception:
                pass

    _thread = threading.Thread(target=_loop, name="chat-scheduler", daemon=True)
    _thread.start()


def stop_scheduler() -> None:
    _stop.set()


def _tick() -> None:
    from .ws_hub import get_main_loop

    try:
        from .companion.followup import FollowupService

        FollowupService().expire_due()
    except Exception:
        pass

    due = sched.due_schedules()
    if not due:
        return
    # 按用户串行，避免同一用户并发打爆；不同用户互不影响
    by_user: dict = {}
    for row in due:
        by_user.setdefault(row["user_id"], []).append(row)

    main = get_main_loop()
    for user_id, rows in by_user.items():
        for row in rows:
            try:
                coro = sched.fire_schedule(dict(row))
                if main is not None and main.is_running():
                    fut = asyncio.run_coroutine_threadsafe(coro, main)
                    fut.result(timeout=180)
                else:
                    asyncio.run(coro)
            except Exception:
                try:
                    sched._record_run(row["id"], user_id, "error", "tick failed")
                    sched._advance_next(row)
                except Exception:
                    pass
