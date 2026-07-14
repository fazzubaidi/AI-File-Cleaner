"""Delete, quarantine, restore, and archive files safely."""
from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from config import QUARANTINE_DIR, ensure_app_dirs

try:
    from send2trash import send2trash
    HAS_TRASH = True
except ImportError:
    HAS_TRASH = False


@dataclass
class DeleteResult:
    deleted: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    restore_map: dict[str, str] = field(default_factory=dict)
    mode: str = "trash"

    @property
    def summary(self) -> str:
        verb = {"trash": "Sent to Recycle Bin", "quarantine": "Quarantined",
                "permanent": "Permanently deleted"}.get(self.mode, "Deleted")
        msg = f"{verb}: {len(self.deleted)} file(s)."
        if self.failed:
            msg += f" {len(self.failed)} failed (locked or missing)."
        return msg


def delete_files(paths: Iterable[Path], use_trash: bool = True,
                 mode: Optional[str] = None) -> DeleteResult:
    if mode is None:
        mode = "trash" if use_trash else "permanent"
    if mode == "trash" and not HAS_TRASH:
        mode = "permanent"

    result = DeleteResult(mode=mode)
    batch_dir: Optional[Path] = None
    if mode == "quarantine":
        ensure_app_dirs()
        batch_dir = QUARANTINE_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir.mkdir(parents=True, exist_ok=True)

    for path in paths:
        path = Path(path)
        try:
            if not path.exists():
                result.failed.append((path, "File no longer exists"))
                continue
            if mode == "trash":
                send2trash(str(path))
            elif mode == "quarantine":
                dest = _unique_path(batch_dir / path.name)
                shutil.move(str(path), str(dest))
                result.restore_map[str(path)] = str(dest)
            else:
                os.remove(path)
            result.deleted.append(path)
        except PermissionError:
            result.failed.append((path, "Permission denied (file may be in use)"))
        except (OSError, shutil.Error) as exc:
            result.failed.append((path, str(exc)))
    return result


def restore_files(restore_map: dict[str, str]) -> DeleteResult:
    result = DeleteResult(mode="restore")
    for original, quarantined in restore_map.items():
        src, dst = Path(quarantined), Path(original)
        try:
            if not src.exists():
                result.failed.append((dst, "Quarantined copy is missing"))
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(_unique_path(dst)))
            result.deleted.append(dst)
        except (OSError, shutil.Error) as exc:
            result.failed.append((dst, str(exc)))
    return result


def archive_files(paths: Iterable[Path], archive_path: Path,
                  base_folder: Optional[Path] = None,
                  remove_originals: bool = True) -> tuple[Optional[Path], DeleteResult]:
    paths = [Path(p) for p in paths]
    archive_path = Path(archive_path)
    archived: list[Path] = []
    result = DeleteResult(mode="archive")

    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in paths:
                try:
                    arcname = _arcname(path, base_folder)
                    zf.write(path, arcname)
                    archived.append(path)
                except (PermissionError, OSError) as exc:
                    result.failed.append((path, f"Not archived: {exc}"))
    except (OSError, zipfile.BadZipFile) as exc:
        result.failed.extend((p, f"Archive failed: {exc}") for p in paths)
        return None, result

    if remove_originals and archived:
        removal = delete_files(archived, mode="trash" if HAS_TRASH else "permanent")
        result.deleted = removal.deleted
        result.failed.extend(removal.failed)
    else:
        result.deleted = archived
    return archive_path, result


def _arcname(path: Path, base: Optional[Path]) -> str:
    if base:
        try:
            return str(path.relative_to(base))
        except ValueError:
            pass
    return path.name


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}_{datetime.now().timestamp():.0f}{path.suffix}")
