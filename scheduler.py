"""In-app scheduled scans.

The schedule lives in settings.json. While the app is open, the GUI polls
`is_due()` every few minutes; when a scan is due it runs in the background
(silently if configured) and raises a notification when reclaimable space
or duplicate counts exceed the user's thresholds.

Note: scans only run while the application is open. For fully unattended
runs, add `python main.py --scheduled` to Windows Task Scheduler or cron
(see README).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from config import load_settings, save_settings

_INTERVALS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
}


def get_schedule() -> dict:
    return load_settings()["schedule"]


def set_schedule(frequency: str, silent: bool, notify_space_gb: float,
                 notify_dup_files: int) -> None:
    settings = load_settings()
    settings["schedule"].update({
        "frequency": frequency,
        "silent": silent,
        "notify_space_gb": notify_space_gb,
        "notify_dup_files": notify_dup_files,
    })
    save_settings(settings)


def is_due(now: Optional[datetime] = None) -> bool:
    """True when a scheduled scan should run."""
    sched = get_schedule()
    interval = _INTERVALS.get(sched.get("frequency"))
    if interval is None:
        return False
    last = sched.get("last_run")
    if not last:
        return True
    try:
        return (now or datetime.now()) - datetime.fromisoformat(last) >= interval
    except ValueError:
        return True


def mark_ran(now: Optional[datetime] = None) -> None:
    settings = load_settings()
    settings["schedule"]["last_run"] = (now or datetime.now()).isoformat(
        timespec="seconds")
    save_settings(settings)


def build_notification(reclaimable_bytes: int, dup_files: int) -> Optional[str]:
    """Message to show after a scheduled scan, or None if under thresholds."""
    sched = get_schedule()
    lines = []
    space_gb = reclaimable_bytes / (1024 ** 3)
    if space_gb >= float(sched.get("notify_space_gb", 1.0)):
        lines.append(f"Reclaimable space has reached {space_gb:.1f} GB.")
    if dup_files >= int(sched.get("notify_dup_files", 25)):
        lines.append(f"{dup_files} duplicate files detected.")
    return "\n".join(lines) if lines else None
