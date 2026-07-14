"""User-defined exclusion rules: folders, extensions, and keywords.

Stored in ~/.ai_file_cleaner/exclusions.json and importable/exportable
as plain JSON so rules can be shared between machines.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from config import EXCLUSIONS_FILE, load_json, save_json


@dataclass
class Exclusions:
    folders: list[str] = field(default_factory=list)     # path fragments
    extensions: list[str] = field(default_factory=list)  # ".py", ".docx"
    keywords: list[str] = field(default_factory=list)    # "resume", "tax"

    def normalized(self) -> "Exclusions":
        return Exclusions(
            folders=[f.strip().replace("\\", "/").lower().strip("/")
                     for f in self.folders if f.strip()],
            extensions=[(e.strip().lower() if e.strip().startswith(".")
                         else "." + e.strip().lower())
                        for e in self.extensions if e.strip()],
            keywords=[k.strip().lower() for k in self.keywords if k.strip()],
        )

    def is_excluded(self, path: Path) -> bool:
        """True when a file or folder matches any rule."""
        rules = self.normalized()
        p = str(path).replace("\\", "/").lower()
        name = path.name.lower()
        if any(frag and frag in p for frag in rules.folders):
            return True
        if path.suffix.lower() in rules.extensions:
            return True
        if any(k in name for k in rules.keywords):
            return True
        return False

    # ------------------------------------------------------------ storage

    def to_dict(self) -> dict:
        return {"folders": self.folders, "extensions": self.extensions,
                "keywords": self.keywords}

    @classmethod
    def from_dict(cls, data: dict) -> "Exclusions":
        return cls(
            folders=list(data.get("folders", [])),
            extensions=list(data.get("extensions", [])),
            keywords=list(data.get("keywords", [])),
        )

    def save(self) -> bool:
        return save_json(EXCLUSIONS_FILE, self.to_dict())

    @classmethod
    def load(cls) -> "Exclusions":
        return cls.from_dict(load_json(EXCLUSIONS_FILE, {}))

    def export_to(self, path: Path) -> bool:
        return save_json(path, self.to_dict())

    @classmethod
    def import_from(cls, path: Path) -> "Exclusions":
        return cls.from_dict(load_json(path, {}))
