# AI File Cleaner — Organization Assistant (v3)

A desktop app that understands, organizes, and cleans your files — not just describes them. It finds stale files, detects duplicates at five levels, groups everything into smart life categories, recommends where each file belongs, scores folder health, rates file importance, answers questions in a chat panel, and lets you clean up with previews, confirmations, and undo at every step.

> Screenshot placeholders: `docs/screenshot-dashboard.png`, `docs/screenshot-files.png`, `docs/screenshot-organize.png`, `docs/screenshot-chat.png`

## Setup

Requires Python 3.10+ (tkinter ships with the standard installer).

```bash
pip install -r requirements.txt
```

Optional — set your Anthropic API key for richer AI explanations, recommendations, and chat answers. Everything still works offline without it:

```bash
# Windows (PowerShell)
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Run

```bash
python main.py               # GUI
python main.py --scheduled   # headless scan for Task Scheduler / cron
```

## What's in the window

**Toolbar** — folder picker, staleness cutoff, Scan (F5) / Cancel (Esc), the natural-language search bar, the ⚡ Actions menu, and filter Presets. Active filters appear as chips underneath; click a chip to clear it.

### 📊 Dashboard (Feature 1 & 9)
Cards for files/folders/storage scanned, unused 30+/90+/1-year+ counts, estimated cleanup size, duplicates and their wasted bytes, empty folders, largest file and folder — plus per-type cards (Images, PDFs, Word, Excel, Videos, Audio, Code, Archives, Unknown), storage-by-extension and storage-by-category charts, the top-10 largest files, and oldest/newest/most-duplicated summaries.

### 🗂 Files (Features 2, 6, 7)
Every stale file with size, dates, **Importance stars** (★★★★★ Critical → ★ Disposable), health score, recommendation, and an AI explanation that always states its evidence ("This appears to be a resume **because** the filename contains Resume and it is a Word document in Downloads"). Toggle **Group by category** to collapse the list into School 🎓, Programming 💻, Finance 💰, Work 💼, Personal 🪪, Pictures 🖼, Videos 🎬, Music 🎵, Documents 📄, Downloads ⬇, Archives 📦, and Other ❓. Critical files render in red. Click any column to sort (importance included). Right-click for open/exclude actions.

### ♊ Duplicates (Feature 4)
Five detection levels, each labeled per group: **Exact** (SHA-256, size-first with cached hashes), **Identical images** (exact-hash image groups), **Same name** (identical filename in different folders), **Near name** (`Resume.pdf` / `Resume (1).pdf` / `Resume FINAL.pdf`), and **Similar documents** (same name stem + extension with close sizes — contents never leave your machine). Check individual copies, or use "Check all duplicates (keep originals)" which only auto-selects byte-identical extras.

### 🧭 Organize (Feature 3)
Every file that would be better off elsewhere gets: Current Location → **Suggested Folder**, a reason, and a color-coded **Confidence** score (green ≥80%). Nothing ever moves automatically: check rows (or "Select all ≥80%"), **Preview Changes**, **Apply Changes**, and **Undo** the whole batch afterwards. Moves are recorded in Cleanup History.

### 🩺 Folders (Feature 5)
Each folder scores 0–100 with findings ("112 unused files", "26 duplicates", "mixed content") and a recommendation. Red < 50, amber < 80, green healthy.

### 📅 Timeline (Feature 11)
Browse stale files by Year → Month, newest first.

### 🤖 AI Chat (Feature 12)
A side panel (Ctrl+J) that answers from the **existing scan** — no rescans: "What can I safely delete?", "Show duplicate resumes", "Which folders are the messiest?", "How much space can I recover?", "Files I haven't opened in 5 years". Offline it uses intent matching plus the search engine; with an API key, unmatched questions go to Claude with a compact stats summary (never file contents).

### 🔎 Natural-language search (Feature 8)
Type queries like `old resumes`, `python projects`, `college homework`, `vacation pictures`, `unused PDFs`, `largest videos` — parsed into category/extension/age/size constraints with synonym and plural handling.

### ⚡ One-click actions (Feature 10)
Delete empty folders · Delete screenshots older than 2 years · Clear temp files & old installers · Archive files unused for 1 year+ · Organize Downloads · Move resumes together. **Every action shows a full preview list and requires confirmation** before anything happens, then flows through history/undo like manual cleanups.

## Deleting, archiving, undo (Feature 15 — safety)

Three delete modes (footer): **Recycle Bin** (OS bin), **Quarantine** (in-app undo), **Permanent** (irreversible, explicit). Every destructive action runs the **Cleanup Simulator** first. **Undo Last** (Ctrl+Z) restores the latest quarantine batch or organization move batch. Tools → Cleanup History shows every batch with CSV export and per-batch restore. All actions are logged to `~/.ai_file_cleaner/app.log`.

## Keyboard shortcuts

F5 scan · Esc cancel · Ctrl+F search · Ctrl+A select all visible · Delete delete selected · Ctrl+Z undo · Ctrl+D dark mode · Ctrl+J chat panel

## Performance (Feature 14)

Concurrent folder traversal, thread-pool hashing with a persistent hash cache, cached AI responses, background AI analysis, cancelable scans, and incremental row rendering (250 rows per tick) so the UI never freezes. Window size, dark mode, and grouping preference persist between sessions.

## Module layout (Feature 16)

| File | Responsibility |
|---|---|
| `main.py` | Entry point; `--scheduled` headless mode |
| `config.py` | Settings, paths, taxonomy, type buckets, tunables |
| `scanner.py` | Threaded traversal, stale detection, full statistics |
| `duplicates.py` | 5-level duplicate detection + hash cache |
| `categorizer.py` | Smart life-category classification |
| `recommendations.py` | Recommendations, health scores, importance stars |
| `org_advisor.py` | Suggested folders with reasons and confidence |
| `folder_health.py` | Per-folder 0–100 health scoring |
| `search_engine.py` | Natural-language search parsing and matching |
| `quick_actions.py` | One-click action planning (preview-first) |
| `chat_assistant.py` | Scan-aware chat (offline intents + optional Claude) |
| `ai_analyzer.py` | Claude analysis w/ evidence-based fallback + cache |
| `analytics.py` | Dashboard statistics and cleanup simulation |
| `filters.py` | Combinable filters and presets |
| `exclusions.py` | Exclusion rules with JSON import/export |
| `file_actions.py` | Trash/quarantine/permanent delete, restore, ZIP |
| `history.py` | Cleanup + move history, CSV export, undo records |
| `organizer.py` | Manual organize-by-scheme dialog backend |
| `scheduler.py` | Scheduled scans and threshold notifications |
| `applog.py` | Application logging |
| `theme.py` | Light/dark palettes + v3 widget styles |
| `dialogs.py` | Exclusions/history/scheduler/organizer/preview windows |
| `gui.py` | Main window, tabs, chat panel, threading |

## Privacy & safety

- Only file **names, paths, sizes, dates, and aggregate stats** ever go to the API — never file contents. Offline mode sends nothing anywhere.
- Nothing is deleted or moved without a preview and confirmation; undo covers quarantine deletions and organization moves.
- App data lives in `~/.ai_file_cleaner/`; scans cap at 5,000 stale files (`MAX_FILES_PER_SCAN`).
- The v1/v2 APIs (`scan_folders()`, `delete_files(paths, use_trash=...)`) still work.
