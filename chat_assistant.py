"""Side-panel chat assistant that answers from scan results — no rescans.

Fully functional offline through intent matching + the search engine.
With an ANTHROPIC_API_KEY, unmatched questions are sent to Claude along
with a compact scan summary (names/paths/stats only — never contents).
"""
from __future__ import annotations

import json
import os
import re

from analytics import DashboardStats
from folder_health import FolderScore
from scanner import FileInfo, human_size
from search_engine import search


def _fmt_files(files: list[FileInfo], limit: int = 8) -> str:
    lines = [f"  • {f.name} ({f.size_human}, idle {f.days_idle}d) — {f.path.parent}"
             for f in files[:limit]]
    if len(files) > limit:
        lines.append(f"  … and {len(files) - limit} more")
    return "\n".join(lines) if lines else "  (none)"


class ChatAssistant:
    def __init__(self):
        self.files: list[FileInfo] = []
        self.stats = DashboardStats()
        self.folder_scores: list[FolderScore] = []

    def update_context(self, files, stats, folder_scores) -> None:
        self.files, self.stats, self.folder_scores = files, stats, folder_scores

    # ------------------------------------------------------------ answer

    def answer(self, question: str) -> str:
        if not self.files and not self.stats.total_files:
            return "Run a scan first — I answer using the latest scan results."
        q = question.lower()

        if re.search(r"safe(ly)?\s+delete|what can i delete", q):
            safe = [f for f in self.files if f.recommendation in
                    ("Safe to Delete", "Temporary", "Duplicate", "Installer")]
            size = human_size(sum(f.size_bytes for f in safe))
            return (f"{len(safe)} file(s) look safe to delete "
                    f"(duplicates, temp files, installers) — about {size}:\n"
                    + _fmt_files(sorted(safe, key=lambda f: f.size_bytes,
                                        reverse=True)))

        if "space" in q or "recover" in q or "reclaim" in q:
            return (f"Estimated reclaimable space: "
                    f"{human_size(max(0, self.stats.reclaimable_bytes))} "
                    f"across {self.stats.stale_files} stale file(s). "
                    f"Duplicates alone waste "
                    f"{human_size(self.stats.dup_wasted_bytes)}.")

        if "messiest" in q or ("folder" in q and ("worst" in q or "messy" in q)):
            worst = self.folder_scores[:5]
            if not worst:
                return "No folder scores yet — run a scan."
            lines = [f"  • {s.name}: {s.score}/100 — {'; '.join(s.reasons[:2])}"
                     for s in worst]
            return "Messiest folders:\n" + "\n".join(lines)

        m = re.search(r"(\d+)\s*year", q)
        if m and ("open" in q or "used" in q or "touch" in q):
            years = int(m.group(1))
            old = [f for f in self.files if f.days_idle >= years * 365]
            return (f"{len(old)} file(s) untouched for {years}+ years:\n"
                    + _fmt_files(sorted(old, key=lambda f: f.days_idle,
                                        reverse=True)))

        if "duplicate" in q:
            dups = [f for f in self.files if f.dup_group and not f.is_dup_keeper]
            extra = q.replace("duplicate", "").replace("show", "").strip(" s.?")
            if extra:
                dups = [f for f in dups if extra in str(f.path).lower()]
            wasted = human_size(sum(f.size_bytes for f in dups))
            return (f"{len(dups)} duplicate cop(ies) found ({wasted} wasted):\n"
                    + _fmt_files(dups))

        if "empty folder" in q:
            n = len(self.stats.empty_dirs)
            return (f"{n} empty folder(s) found. Use Actions → Delete empty "
                    "folders to remove them." if n else "No empty folders found.")

        # generic: run the natural-language search
        results, desc = search(self.files, question)
        if results:
            return f"{desc}:\n" + _fmt_files(results)

        # last resort: ask Claude with a compact context (if key present)
        api = self._ask_api(question)
        if api:
            return api
        return ("I couldn't match that to the scan data. Try things like "
                "'what can I safely delete?', 'show duplicate resumes', "
                "'largest videos', or 'files I haven't opened in 5 years'.")

    # ------------------------------------------------------------ API

    def _ask_api(self, question: str) -> str:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return ""
        try:
            import anthropic
            from config import ANTHROPIC_MODEL
            summary = {
                "total_files": self.stats.total_files,
                "total_storage": human_size(self.stats.total_bytes),
                "stale_files": self.stats.stale_files,
                "reclaimable": human_size(max(0, self.stats.reclaimable_bytes)),
                "duplicates": self.stats.dup_files,
                "worst_folders": [
                    {"folder": s.name, "score": s.score}
                    for s in self.folder_scores[:5]],
                "sample_stale_files": [
                    {"name": f.name, "size": f.size_human,
                     "idle_days": f.days_idle,
                     "category": f.smart_category,
                     "recommendation": f.recommendation}
                    for f in self.files[:60]],
            }
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model=ANTHROPIC_MODEL, max_tokens=600,
                system=("You are a file-cleanup assistant inside a desktop "
                        "app. Answer briefly using ONLY the scan summary "
                        "provided. Never invent files."),
                messages=[{"role": "user", "content":
                           json.dumps(summary) + "\n\nQuestion: " + question}])
            return resp.content[0].text.strip()
        except Exception:
            return ""
