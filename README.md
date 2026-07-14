# AI File Cleaner

A desktop app that finds files you haven't touched in 30+ days, detects exact and near-duplicate files, scores every file's "health," explains each file in plain English via the Claude API, and lets you delete, quarantine, archive, or organize only the files you check — with a full analytics dashboard, cleanup history, and undo.

## Setup

Requires Python 3.10+ (tkinter ships with the standard installer).

```bash
pip install -r requirements.txt
```

Optional but recommended — enable AI explanations and recommendations by setting your Anthropic API key:

```bash
# Windows (PowerShell)
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
```

Without a key, the app falls back to a built-in rule engine and extension dictionary, so every feature still works offline.

## Run

```bash
python main.py               # GUI
python main.py --scheduled   # headless scan for Task Scheduler / cron
```

## Feature guide

### Scanning
Press **Scan** to check Downloads, Documents, and Desktop, or **Choose Folder…** first. The "Unused ≥ N days" spinner sets the staleness cutoff (a file is stale only when *both* its access and modification times exceed it). Folders scan concurrently on a thread pool; **Cancel** stops mid-scan. Permission-locked files are skipped, never crashed on.

### Duplicate detection (Duplicates tab)
Files are grouped by size first, and only size collisions get SHA-256 hashed (on a thread pool, with hashes cached in `~/.ai_file_cleaner/hash_cache.json` so unchanged files are never re-hashed). Each group shows the **Original** (earliest-modified copy), its **Duplicates**, and the wasted bytes. **Check all duplicates (keep originals)** selects every extra copy in one click.

### Similar file detection
Filename normalization plus fuzzy matching groups version families like `Report.pdf` / `Report (1).pdf` / `Report-final.pdf` or `Resume_v2.docx` / `Resume_v3.docx`, labeled **Multiple versions** or **Possible duplicate**. Filter to them with the **Similar** checkbox.

### Dashboard tab
Cards show files/storage scanned, stale files, duplicate files, space wasted by duplicates, reclaimable space, and the largest file and folder. Below: bar charts of storage by extension and by category, plus the top 10 largest files.

### AI recommendations & health score
Every file gets a recommendation — **Safe to Delete, Probably Safe, Review Recommended, Important, Duplicate, Temporary, Installer, Archive Candidate** — based on age, access/modification times, extension, folder, duplicate status, and filename patterns (files named like `tax`, `resume`, `invoice` are flagged **Important**). Every file also gets a 0–100 **Health** score (high = keep, low = excellent deletion candidate). Click a row to see the full path, the reasoning, and the health factors. With an API key, Claude refines the recommendation and explanation; responses are cached in `ai_cache.json`.

### Smart filtering
Combine the search bar with the filter row (category, recommendation, extension, min size, Dups / Similar / >100MB / Old 1y+) — all filters AND together. The **Presets** menu includes **Downloads cleanup** (installers, temp files, duplicates, downloaded archives), **Large files only**, and **Duplicates only**. Click any column header to sort; click again to reverse.

### Exclusion rules
**Tools → Exclusion Rules…** lets you exclude folders (path fragments like `Documents/School`), extensions (`.py`, `.docx`), and keywords (`resume`, `tax`). Rules support **Import/Export** as JSON and are stored in `~/.ai_file_cleaner/exclusions.json`. Right-click any row → **Exclude this folder from scans** for a one-click rule.

### Deleting — three modes

| Mode | What happens | Undo |
|---|---|---|
| Recycle Bin | `send2trash` to the OS bin | Restore manually from the OS Recycle Bin |
| Quarantine | Moved to `~/.ai_file_cleaner/quarantine/` | **Undo Last Cleanup** / restore from History |
| Permanent | Removed immediately | None — irreversible |

Every deletion first opens the **Cleanup Simulator**: files removed, storage recovered, duplicates removed, and what remains — nothing is touched until you confirm.

### Undo & cleanup history
**Tools → Cleanup History…** lists every cleanup (timestamp, mode, file count, size recovered, duplicates removed) with per-file detail, **Export CSV**, and **Restore Selected Cleanup** for quarantine records. **Undo Last Cleanup** (footer button or Tools menu) restores the most recent quarantine batch. History lives in `~/.ai_file_cleaner/history.json`.

### Archive instead of delete
**Archive Selected** compresses checked files into a ZIP (folder structure preserved below their common root, destination of your choice), then sends the originals to the Recycle Bin — only files that made it into the archive are removed.

### Automatic organization
**Tools → Organize Files…** plans moves by **category** (AI-detected type), **extension**, **year**, **month**, or **project** (shared name stems). The full move list is previewed; nothing moves until you press **Apply Moves**. Name collisions get `(1)`, `(2)` suffixes.

### Scheduled scans
**Tools → Scheduled Scans…** — daily, weekly, or monthly, silent or not, with alerts when reclaimable space exceeds X GB or duplicates exceed X files. Scheduled scans run while the app is open. For fully unattended runs, schedule `python main.py --scheduled` with Windows Task Scheduler or cron.

### Interface
Light/dark mode (**View → Dark Mode**, remembered between sessions), sortable columns, right-click context menu (open file, open folder, toggle, exclude folder), live search, progress bar, status bar, and responsive resizing.

## Module layout

| File | Responsibility |
|---|---|
| `main.py` | Entry point; `--scheduled` headless mode |
| `config.py` | Settings, app-data paths, file taxonomy, tunables |
| `scanner.py` | Threaded traversal, stale detection, storage stats |
| `duplicates.py` | SHA-256 duplicates (cached), similar-file grouping |
| `analytics.py` | Dashboard statistics and cleanup simulation |
| `recommendations.py` | Rule-based recommendations and health scores |
| `ai_analyzer.py` | Claude API analysis with offline fallback and cache |
| `filters.py` | Combinable smart filters and presets |
| `exclusions.py` | Exclusion rules with JSON import/export |
| `file_actions.py` | Trash / quarantine / permanent delete, restore, ZIP archive |
| `history.py` | Cleanup history, CSV export, undo records |
| `organizer.py` | Move planning and execution |
| `scheduler.py` | Schedule storage, due checks, notifications |
| `theme.py` | Light/dark ttk palettes |
| `dialogs.py` | Exclusions, history, scheduler, organizer, simulator windows |
| `gui.py` | Main window, tabs, threading, all user actions |

## Privacy & safety notes

- Only file **names, paths, sizes, and dates** are sent to the Claude API — never file contents. Offline mode sends nothing anywhere.
- All app data (settings, caches, history, quarantine) stays in `~/.ai_file_cleaner/` on your machine.
- Scans cap at 5,000 stale files per run (`MAX_FILES_PER_SCAN` in `config.py`).
- The legacy `scan_folders()` and `delete_files(paths, use_trash=...)` APIs still work, so any scripts built on v1 keep running.
