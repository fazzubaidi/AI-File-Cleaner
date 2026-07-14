"""AI File Cleaner — entry point."""
from __future__ import annotations

import sys


def _headless_scheduled_scan() -> None:
    import scheduler
    from analytics import build_stats
    from config import DEFAULT_FOLDERS, STALE_DAYS
    from duplicates import find_duplicates
    from exclusions import Exclusions
    from recommendations import apply_rules
    from scanner import human_size, scan_with_stats

    rules = Exclusions.load()
    stale, raw = scan_with_stats(DEFAULT_FOLDERS, STALE_DAYS,
                                 is_excluded=rules.is_excluded)
    groups = find_duplicates(stale)
    apply_rules(stale)
    stats = build_stats(stale, raw, groups)
    scheduler.mark_ran()
    print(f"Scanned {stats.total_files} files ({human_size(stats.total_bytes)}).")
    print(f"Stale: {stats.stale_files} | duplicate groups: {len(groups)} | "
          f"reclaimable: {human_size(max(0, stats.reclaimable_bytes))}")
    note = scheduler.build_notification(max(0, stats.reclaimable_bytes),
                                        stats.dup_files)
    if note:
        print("ALERT:", note.replace("\n", " "))


if __name__ == "__main__":
    if "--scheduled" in sys.argv:
        _headless_scheduled_scan()
    else:
        from gui import run
        run()
