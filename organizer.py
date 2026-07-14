"""Optional file organization: plan moves, preview, then apply.

Schemes:
  category  — Documents/, Images/, Video/, ... (AI-detected category)
  extension — pdf/, zip/, docx/, ...
  year      — 2024/, 2025/, ...
  month     — 2025-11/, 2026-01/, ...
  project   — files sharing a normalized name stem get their own folder

Nothing moves until apply_moves() runs; the GUI shows the full plan first.
"""
from __future__ import annotations

import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from duplicates import normalize_stem
from file_actions import _unique_path
from scanner import FileInfo

SCHEMES = ("category", "extension", "year", "month", "project")


@dataclass
class MovePlan:
    src: Path
    dst: Path

    @property
    def unchanged(self) -> bool:
        return self.src.parent == self.dst.parent


def plan_moves(files: list[FileInfo], dest_root: Path,
               scheme: str = "category") -> list[MovePlan]:
    """Compute where every file would go. Never touches the disk."""
    dest_root = Path(dest_root)
    plans: list[MovePlan] = []

    if scheme == "project":
        stems: dict[str, list[FileInfo]] = defaultdict(list)
        for f in files:
            stems[normalize_stem(f.name) or "misc"].append(f)
        for stem, members in stems.items():
            folder = stem.title()[:40] if len(members) > 1 else "Misc"
            for f in members:
                plans.append(MovePlan(f.path, dest_root / folder / f.name))
        return [p for p in plans if not p.unchanged]

    for f in files:
        if scheme == "extension":
            sub = f.extension.lstrip(".") or "no_extension"
        elif scheme == "year":
            sub = f.last_modified.strftime("%Y")
        elif scheme == "month":
            sub = f.last_modified.strftime("%Y-%m")
        else:  # category (default)
            sub = f.category
        plans.append(MovePlan(f.path, dest_root / sub / f.name))
    return [p for p in plans if not p.unchanged]


def apply_moves(plans: list[MovePlan]) -> tuple[int, list[tuple[Path, str]]]:
    """Execute a reviewed plan. Returns (moved count, [(path, error), ...])."""
    moved, failed = 0, []
    for plan in plans:
        try:
            if not plan.src.exists():
                failed.append((plan.src, "File no longer exists"))
                continue
            plan.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(plan.src), str(_unique_path(plan.dst)))
            moved += 1
        except (OSError, shutil.Error) as exc:
            failed.append((plan.src, str(exc)))
    return moved, failed
