"""Main window: dashboard, file table, duplicate groups, and all actions."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import history as history_mod
import scheduler
from ai_analyzer import analyze_files
from analytics import DashboardStats, build_stats
from config import (DEFAULT_FOLDERS, FILE_CATEGORIES, LARGE_FILE_MB,
                    RECOMMENDATION_LEVELS, STALE_DAYS, ensure_app_dirs,
                    load_settings, save_settings)
from dialogs import (ExclusionsDialog, HistoryWindow, OrganizerDialog,
                     SchedulerDialog, show_simulator)
from duplicates import DuplicateGroup, find_duplicates, find_similar
from exclusions import Exclusions
from file_actions import HAS_TRASH, archive_files, delete_files, restore_files
from filters import (FilterState, apply_filters, preset_downloads_cleanup,
                     preset_duplicates, preset_large_files)
from recommendations import apply_rules
from scanner import FileInfo, human_size, scan_with_stats
from theme import apply_theme

CHECKED, UNCHECKED = "☑", "☐"
SCHEDULE_POLL_MS = 5 * 60 * 1000

DELETE_MODES = [("Recycle Bin (recoverable)", "trash"),
                ("Quarantine (undoable in-app)", "quarantine"),
                ("Permanent (irreversible)", "permanent")]


class FileCleanerApp:
    def __init__(self, root: tk.Tk):
        ensure_app_dirs()
        self.root = root
        self.root.title("AI File Cleaner")
        self.root.geometry("1240x760")
        self.root.minsize(960, 560)

        self.settings = load_settings()
        self.palette = apply_theme(root, self.settings.get("dark_mode", False))

        self.all_files: list[FileInfo] = []
        self.dup_groups: list[DuplicateGroup] = []
        self.stats = DashboardStats()
        self.checked: set[int] = set()
        self.item_to_file: dict[str, FileInfo] = {}
        self.dup_item_to_file: dict[str, FileInfo] = {}
        self.filter_state = FilterState()
        self.sort_col, self.sort_desc = "size", True
        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self.custom_folder: Path | None = None
        self.scheduled_run = False

        self._build_menu()
        self._build_toolbar()
        self._build_notebook()
        self._build_footer()
        self.root.after(120, self._poll_queue)
        self.root.after(4000, self._schedule_tick)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="Exclusion Rules…", command=lambda: ExclusionsDialog(self.root))
        tools.add_command(label="Scheduled Scans…", command=lambda: SchedulerDialog(self.root))
        tools.add_command(label="Organize Files…", command=self.open_organizer)
        tools.add_separator()
        tools.add_command(label="Cleanup History…", command=lambda: HistoryWindow(self.root))
        tools.add_command(label="Undo Last Cleanup", command=self.undo_last)
        menubar.add_cascade(label="Tools", menu=tools)

        view = tk.Menu(menubar, tearoff=0)
        self.dark_var = tk.BooleanVar(value=self.settings.get("dark_mode", False))
        view.add_checkbutton(label="Dark Mode", variable=self.dark_var,
                             command=self.toggle_dark)
        menubar.add_cascade(label="View", menu=view)
        self.root.config(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill="x")

        ttk.Button(bar, text="📁 Choose Folder…", command=self.choose_folder).pack(side="left")
        self.folder_label = ttk.Label(bar, text="Default: Downloads, Documents, Desktop",
                                      style="Muted.TLabel")
        self.folder_label.pack(side="left", padx=8)

        ttk.Label(bar, text="Unused ≥").pack(side="left", padx=(12, 3))
        self.days_var = tk.IntVar(value=STALE_DAYS)
        ttk.Spinbox(bar, from_=1, to=3650, width=5, textvariable=self.days_var).pack(side="left")
        ttk.Label(bar, text="days").pack(side="left", padx=(3, 12))

        self.scan_btn = ttk.Button(bar, text="🔍 Scan", command=self.start_scan)
        self.scan_btn.pack(side="left")
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.cancel_scan,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=4)

        ttk.Label(bar, text="Search").pack(side="left", padx=(16, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._on_search())
        ttk.Entry(bar, textvariable=self.search_var, width=22).pack(side="left")

        presets = ttk.Menubutton(bar, text="Presets")
        menu = tk.Menu(presets, tearoff=0)
        menu.add_command(label="Downloads cleanup",
                         command=lambda: self._apply_preset(preset_downloads_cleanup()))
        menu.add_command(label="Large files only",
                         command=lambda: self._apply_preset(preset_large_files()))
        menu.add_command(label="Duplicates only",
                         command=lambda: self._apply_preset(preset_duplicates()))
        menu.add_command(label="Clear filters", command=self._reset_filters)
        presets["menu"] = menu
        presets.pack(side="left", padx=8)

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._build_dashboard_tab()
        self._build_files_tab()
        self._build_dups_tab()
        self.notebook.select(1)

    def _build_dashboard_tab(self) -> None:
        self.dash = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.dash, text="  📊 Dashboard  ")
        self.cards_frame = ttk.Frame(self.dash)
        self.cards_frame.pack(fill="x")
        mid = ttk.Frame(self.dash)
        mid.pack(fill="both", expand=True, pady=(10, 0))
        mid.columnconfigure(0, weight=3)
        mid.columnconfigure(1, weight=2)
        mid.rowconfigure(0, weight=1)
        self.chart = tk.Canvas(mid, highlightthickness=0,
                               bg=self.palette["surface"])
        self.chart.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.top_list = ttk.Treeview(mid, columns=("name", "size"),
                                     show="headings", height=10)
        self.top_list.heading("name", text="Top 10 largest files")
        self.top_list.heading("size", text="Size")
        self.top_list.column("name", width=280)
        self.top_list.column("size", width=90, anchor="e")
        self.top_list.grid(row=0, column=1, sticky="nsew")
        self._render_dashboard()

    def _card(self, parent, title: str, value: str, col: int, row: int) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=10)
        card.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
        parent.columnconfigure(col, weight=1)
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, text=value, style="CardValue.TLabel",
                  wraplength=220).pack(anchor="w")

    def _render_dashboard(self) -> None:
        for w in self.cards_frame.winfo_children():
            w.destroy()
        s = self.stats
        cards = [
            ("Files scanned", f"{s.total_files:,}"),
            ("Storage scanned", human_size(s.total_bytes)),
            ("Stale files", f"{s.stale_files:,}"),
            ("Duplicate files", f"{s.dup_files:,}"),
            ("Wasted by duplicates", human_size(s.dup_wasted_bytes)),
            ("Reclaimable space", human_size(max(0, s.reclaimable_bytes))),
            ("Largest file", f"{s.largest_file[0]}\n{human_size(s.largest_file[1])}"),
            ("Largest folder", f"{Path(s.largest_folder[0]).name or s.largest_folder[0]}"
                               f"\n{human_size(s.largest_folder[1])}"),
        ]
        for i, (title, value) in enumerate(cards):
            self._card(self.cards_frame, title, value, col=i % 4, row=i // 4)

        self.top_list.delete(*self.top_list.get_children())
        for name, size in s.top_files:
            self.top_list.insert("", "end", values=(name, human_size(size)))
        self.root.after(50, self._draw_charts)

    def _draw_charts(self) -> None:
        c = self.chart
        c.delete("all")
        c.configure(bg=self.palette["surface"])
        w = max(c.winfo_width(), 300)
        h = max(c.winfo_height(), 220)
        data = self.stats.ext_breakdown[:8]
        cats = self.stats.cat_breakdown[:6]
        if not data:
            c.create_text(w // 2, h // 2, text="Run a scan to see storage charts",
                          fill=self.palette["muted"], font=("Segoe UI", 11))
            return
        colors = self.palette["chart"]
        half = h // 2
        self._bars(c, data, 0, 0, w, half, "By extension (size)", colors,
                   fmt=lambda t: f"{t[0]} · {human_size(t[1])} · {t[2]} files",
                   value=lambda t: t[1])
        self._bars(c, cats, 0, half, w, half, "By category (size)", colors,
                   fmt=lambda t: f"{t[0]} · {human_size(t[1])}",
                   value=lambda t: t[1])

    def _bars(self, c, rows, x, y, w, h, title, colors, fmt, value) -> None:
        c.create_text(x + 10, y + 12, anchor="w", text=title,
                      fill=self.palette["text"], font=("Segoe UI", 10, "bold"))
        if not rows:
            return
        maxval = max(value(r) for r in rows) or 1
        bar_h = max(10, (h - 30) // max(len(rows), 1) - 6)
        for i, row in enumerate(rows):
            top = y + 26 + i * (bar_h + 6)
            if top + bar_h > y + h:
                break
            length = int((w - 220) * value(row) / maxval)
            c.create_rectangle(x + 10, top, x + 10 + max(length, 2), top + bar_h,
                               fill=colors[i % len(colors)], width=0)
            c.create_text(x + 16 + max(length, 2), top + bar_h // 2, anchor="w",
                          text=fmt(row), fill=self.palette["muted"],
                          font=("Segoe UI", 9))

    def _build_files_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(4, 4))
        self.notebook.add(tab, text="  🗂 Files  ")

        self._build_filter_panel(tab)

        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        cols = ("check", "name", "size", "accessed", "idle", "health",
                "rec", "explanation")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings",
                                 selectmode="extended")
        headings = {
            "check": ("", 34), "name": ("File Name", 200), "size": ("Size", 80),
            "accessed": ("Last Accessed", 110), "idle": ("Idle (d)", 60),
            "health": ("Health", 60), "rec": ("Recommendation", 140),
            "explanation": ("AI Explanation", 380),
        }
        for col, (title, width) in headings.items():
            self.tree.heading(col, text=title,
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=width,
                             stretch=col in ("name", "explanation"),
                             anchor="center" if col in ("check", "size", "idle",
                                                        "health") else "w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Button-2>", self._on_right_click)

        self.detail_var = tk.StringVar(
            value="Select a row for full path, reasoning, and health factors.")
        ttk.Label(tab, textvariable=self.detail_var, padding=(8, 4),
                  wraplength=1180, style="Muted.TLabel").pack(fill="x")

        self.ctx_menu = tk.Menu(self.root, tearoff=0)
        self.ctx_menu.add_command(label="Open file", command=self._ctx_open)
        self.ctx_menu.add_command(label="Open containing folder",
                                  command=self._ctx_open_folder)
        self.ctx_menu.add_command(label="Toggle checkbox", command=self._ctx_toggle)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Exclude this folder from scans",
                                  command=self._ctx_exclude_folder)

    def _build_filter_panel(self, parent) -> None:
        panel = ttk.Frame(parent, padding=(4, 2))
        panel.pack(fill="x")

        ttk.Label(panel, text="Category").pack(side="left")
        self.f_cat = ttk.Combobox(panel, state="readonly", width=12,
                                  values=["All"] + FILE_CATEGORIES)
        self.f_cat.set("All")
        self.f_cat.pack(side="left", padx=(2, 8))

        ttk.Label(panel, text="Recommendation").pack(side="left")
        self.f_rec = ttk.Combobox(panel, state="readonly", width=18,
                                  values=["All"] + RECOMMENDATION_LEVELS)
        self.f_rec.set("All")
        self.f_rec.pack(side="left", padx=(2, 8))

        ttk.Label(panel, text="Ext").pack(side="left")
        self.f_ext = ttk.Entry(panel, width=8)
        self.f_ext.pack(side="left", padx=(2, 8))

        ttk.Label(panel, text="Min MB").pack(side="left")
        self.f_min = ttk.Entry(panel, width=6)
        self.f_min.pack(side="left", padx=(2, 8))

        self.f_dup = tk.BooleanVar()
        self.f_sim = tk.BooleanVar()
        self.f_large = tk.BooleanVar()
        self.f_old = tk.BooleanVar()
        for text, var in [("Dups", self.f_dup), ("Similar", self.f_sim),
                          (f">{LARGE_FILE_MB}MB", self.f_large),
                          ("Old 1y+", self.f_old)]:
            ttk.Checkbutton(panel, text=text, variable=var).pack(side="left", padx=2)

        ttk.Button(panel, text="Apply Filters",
                   command=self._apply_filter_panel).pack(side="left", padx=8)
        ttk.Button(panel, text="Reset", command=self._reset_filters).pack(side="left")

    def _build_dups_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(tab, text="  ♊ Duplicates  ")

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 4))
        self.dup_summary = ttk.Label(top, text="Run a scan to find duplicates.",
                                     style="Muted.TLabel")
        self.dup_summary.pack(side="left")
        ttk.Button(top, text="Check all duplicates (keep originals)",
                   command=self._check_all_dups).pack(side="right")

        cols = ("check", "size", "modified", "role", "path")
        self.dup_tree = ttk.Treeview(tab, columns=cols, show="tree headings")
        for col, title, width in [("#0", "Group", 220), ("check", "", 34),
                                  ("size", "Size", 80),
                                  ("modified", "Modified", 100),
                                  ("role", "Role", 90), ("path", "Path", 480)]:
            self.dup_tree.heading(col, text=title)
            if col == "#0":
                self.dup_tree.column(col, width=width, stretch=False)
            else:
                self.dup_tree.column(col, width=width,
                                     stretch=(col == "path"),
                                     anchor="center" if col in ("check", "size") else "w")
        vsb = ttk.Scrollbar(tab, orient="vertical", command=self.dup_tree.yview)
        self.dup_tree.configure(yscrollcommand=vsb.set)
        self.dup_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.dup_tree.bind("<Button-1>", self._on_dup_click)

    def _populate_dups(self) -> None:
        self.dup_tree.delete(*self.dup_tree.get_children())
        self.dup_item_to_file.clear()
        wasted = sum(g.wasted_bytes for g in self.dup_groups)
        self.dup_summary.config(
            text=f"{len(self.dup_groups)} duplicate group(s) — "
                 f"{human_size(wasted)} wasted by extra copies.")
        for g in self.dup_groups:
            parent = self.dup_tree.insert(
                "", "end",
                text=f"Group #{g.group_id} — {len(g.files)} copies — "
                     f"{human_size(g.wasted_bytes)} wasted", open=True)
            for f in sorted(g.files, key=lambda x: x.last_modified):
                role = "Original" if f.is_dup_keeper else "Duplicate"
                item = self.dup_tree.insert(parent, "end", values=(
                    CHECKED if id(f) in self.checked else UNCHECKED,
                    f.size_human, f.last_modified.strftime("%Y-%m-%d"),
                    role, str(f.path)))
                self.dup_item_to_file[item] = f

    def _on_dup_click(self, event) -> None:
        if self.dup_tree.identify_column(event.x) != "#1":
            return
        item = self.dup_tree.identify_row(event.y)
        f = self.dup_item_to_file.get(item)
        if f:
            self._toggle_file(f)

    def _check_all_dups(self) -> None:
        for g in self.dup_groups:
            for f in g.files:
                if not f.is_dup_keeper:
                    self.checked.add(id(f))
        self._refresh_checks()

    def _build_footer(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill="x")

        ttk.Button(bar, text="Select All", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(bar, text="Select None", command=lambda: self._set_all(False)).pack(side="left", padx=4)

        ttk.Label(bar, text="Delete mode").pack(side="left", padx=(14, 4))
        self.mode_box = ttk.Combobox(bar, state="readonly", width=26,
                                     values=[m[0] for m in DELETE_MODES])
        default_mode = self.settings.get("delete_mode", "trash")
        self.mode_box.set(next((label for label, key in DELETE_MODES
                                if key == default_mode), DELETE_MODES[0][0]))
        self.mode_box.pack(side="left")
        if not HAS_TRASH:
            self.mode_box.set(DELETE_MODES[1][0])

        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self.progress.pack(side="left", padx=14)

        self.delete_btn = ttk.Button(bar, text="🗑 Delete Selected (0)",
                                     style="Danger.TButton",
                                     command=self.delete_selected, state="disabled")
        self.delete_btn.pack(side="right")
        self.archive_btn = ttk.Button(bar, text="📦 Archive Selected",
                                      command=self.archive_selected,
                                      state="disabled")
        self.archive_btn.pack(side="right", padx=6)
        ttk.Button(bar, text="↩ Undo Last Cleanup",
                   command=self.undo_last).pack(side="right", padx=6)

        self.status_var = tk.StringVar(value="Ready. Pick a folder or press Scan.")
        ttk.Label(self.root, textvariable=self.status_var, style="Status.TLabel",
                  anchor="w").pack(fill="x", side="bottom")

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="Choose a folder to scan")
        if folder:
            self.custom_folder = Path(folder)
            self.folder_label.config(text=str(self.custom_folder))

    def start_scan(self, silent: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.tree.delete(*self.tree.get_children())
        self.dup_tree.delete(*self.dup_tree.get_children())
        self.all_files.clear()
        self.checked.clear()
        self.item_to_file.clear()
        self._update_buttons()
        self.cancel_flag.clear()
        self.scan_btn.state(["disabled"])
        self.cancel_btn.state(["!disabled"])
        self.progress.start(12)
        self.scheduled_run = silent

        folders = [self.custom_folder] if self.custom_folder else DEFAULT_FOLDERS
        days = max(1, self.days_var.get())
        self.worker = threading.Thread(target=self._scan_worker,
                                       args=(folders, days), daemon=True)
        self.worker.start()

    def cancel_scan(self) -> None:
        self.cancel_flag.set()

    def _scan_worker(self, folders: list[Path], days: int) -> None:
        put = self.msg_queue.put
        try:
            rules = Exclusions.load()
            stale, raw = scan_with_stats(
                folders, min_age_days=days,
                progress=lambda m: put(("status", m)),
                cancel_check=self.cancel_flag.is_set,
                is_excluded=rules.is_excluded,
            )
            put(("status", "Detecting duplicates..."))
            dup_groups = find_duplicates(stale,
                                         progress=lambda m: put(("status", m)),
                                         cancel_check=self.cancel_flag.is_set)
            put(("status", "Grouping similar files..."))
            find_similar(stale)
            apply_rules(stale)
            stats = build_stats(stale, raw, dup_groups)
            put(("results", (stale, dup_groups, stats)))

            if stale and not self.cancel_flag.is_set():
                analyze_files(stale,
                              progress=lambda m: put(("status", m)),
                              cancel_check=self.cancel_flag.is_set)
                put(("refresh", None))
            put(("done", f"Done. {len(stale)} stale file(s), "
                         f"{len(dup_groups)} duplicate group(s)."))
        except Exception as exc:
            put(("done", f"Scan failed: {exc}"))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload)[:180])
                elif kind == "results":
                    self.all_files, self.dup_groups, self.stats = payload
                    self._repopulate()
                    self._populate_dups()
                    self._render_dashboard()
                elif kind == "refresh":
                    self._refresh_rows()
                elif kind == "done":
                    self.status_var.set(payload)
                    self.scan_btn.state(["!disabled"])
                    self.cancel_btn.state(["disabled"])
                    self.progress.stop()
                    if self.scheduled_run:
                        self._after_scheduled_scan()
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _visible_files(self) -> list[FileInfo]:
        files = apply_filters(self.all_files, self.filter_state)
        keymap = {
            "name": lambda f: f.name.lower(), "size": lambda f: f.size_bytes,
            "accessed": lambda f: f.last_accessed, "idle": lambda f: f.days_idle,
            "health": lambda f: f.health, "rec": lambda f: f.recommendation,
            "explanation": lambda f: f.explanation.lower(),
            "check": lambda f: id(f) in self.checked,
        }
        key = keymap.get(self.sort_col, keymap["size"])
        return sorted(files, key=key, reverse=self.sort_desc)

    def _repopulate(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.item_to_file.clear()
        for f in self._visible_files():
            item = self.tree.insert("", "end", values=self._row_values(f))
            self.item_to_file[item] = f
        self._update_buttons()
        shown = len(self.item_to_file)
        if shown != len(self.all_files):
            self.status_var.set(
                f"Showing {shown} of {len(self.all_files)} stale files (filtered).")

    def _row_values(self, f: FileInfo) -> tuple:
        return (CHECKED if id(f) in self.checked else UNCHECKED, f.name,
                f.size_human, f.last_accessed.strftime("%Y-%m-%d"),
                f.days_idle, f.health, f.recommendation, f.explanation)

    def _refresh_rows(self) -> None:
        for item, f in self.item_to_file.items():
            self.tree.item(item, values=self._row_values(f))

    def _refresh_checks(self) -> None:
        for item, f in self.item_to_file.items():
            self.tree.set(item, "check",
                          CHECKED if id(f) in self.checked else UNCHECKED)
        for item, f in self.dup_item_to_file.items():
            self.dup_tree.set(item, "check",
                              CHECKED if id(f) in self.checked else UNCHECKED)
        self._update_buttons()

    def _sort_by(self, col: str) -> None:
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, True
        self._repopulate()

    def _on_search(self) -> None:
        self.filter_state.query = self.search_var.get().strip()
        self._repopulate()

    def _apply_filter_panel(self) -> None:
        st = self.filter_state
        st.categories = set() if self.f_cat.get() in ("All", "") else {self.f_cat.get()}
        st.levels = set() if self.f_rec.get() in ("All", "") else {self.f_rec.get()}
        ext = self.f_ext.get().strip().lower()
        st.extensions = {e if e.startswith(".") else "." + e
                         for e in ext.replace(",", " ").split()} if ext else set()
        try:
            st.min_size_mb = float(self.f_min.get()) if self.f_min.get().strip() else None
        except ValueError:
            st.min_size_mb = None
        st.duplicates_only = self.f_dup.get()
        st.similar_only = self.f_sim.get()
        st.large_only = self.f_large.get()
        st.old_over_days = 365 if self.f_old.get() else None
        self._repopulate()

    def _apply_preset(self, preset: FilterState) -> None:
        preset.query = self.search_var.get().strip()
        self.filter_state = preset
        self._repopulate()
        self.notebook.select(1)

    def _reset_filters(self) -> None:
        self.filter_state = FilterState()
        self.search_var.set("")
        self.f_cat.set("All")
        self.f_rec.set("All")
        self.f_ext.delete(0, "end")
        self.f_min.delete(0, "end")
        for var in (self.f_dup, self.f_sim, self.f_large, self.f_old):
            var.set(False)
        self._repopulate()

    def _on_click(self, event) -> None:
        if self.tree.identify_column(event.x) == "#1":
            item = self.tree.identify_row(event.y)
            f = self.item_to_file.get(item)
            if f:
                self._toggle_file(f)

    def _on_space(self, _event) -> None:
        for item in self.tree.selection():
            f = self.item_to_file.get(item)
            if f:
                self._toggle_file(f)

    def _on_select(self, _event) -> None:
        sel = self.tree.selection()
        f = self.item_to_file.get(sel[0]) if sel else None
        if f:
            self.detail_var.set(
                f"{f.path}   |   Why: {f.rec_reason}   |   "
                f"Health {f.health}: {f.health_reason}")

    def _on_right_click(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.ctx_menu.tk_popup(event.x_root, event.y_root)

    def _ctx_file(self):
        sel = self.tree.selection()
        return self.item_to_file.get(sel[0]) if sel else None

    def _ctx_open(self) -> None:
        f = self._ctx_file()
        if f:
            self._os_open(f.path)

    def _ctx_open_folder(self) -> None:
        f = self._ctx_file()
        if f:
            self._os_open(f.path.parent)

    def _ctx_toggle(self) -> None:
        f = self._ctx_file()
        if f:
            self._toggle_file(f)

    def _ctx_exclude_folder(self) -> None:
        f = self._ctx_file()
        if not f:
            return
        rules = Exclusions.load()
        rules.folders.append(str(f.path.parent))
        rules.save()
        self.status_var.set(f"Excluded from future scans: {f.path.parent}")

    @staticmethod
    def _os_open(path: Path) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError:
            pass

    def _toggle_file(self, f: FileInfo) -> None:
        if id(f) in self.checked:
            self.checked.discard(id(f))
        else:
            self.checked.add(id(f))
        self._refresh_checks()

    def _set_all(self, value: bool) -> None:
        for f in self.item_to_file.values():
            self.checked.add(id(f)) if value else self.checked.discard(id(f))
        self._refresh_checks()

    def _update_buttons(self) -> None:
        n = len(self.checked)
        self.delete_btn.config(text=f"🗑 Delete Selected ({n})")
        state = ["!disabled"] if n else ["disabled"]
        self.delete_btn.state(state)
        self.archive_btn.state(state)

    def _checked_files(self) -> list[FileInfo]:
        return [f for f in self.all_files if id(f) in self.checked]

    def _current_mode(self) -> str:
        label = self.mode_box.get()
        return next((key for text, key in DELETE_MODES if text == label), "trash")

    def delete_selected(self) -> None:
        selected = self._checked_files()
        if not selected:
            return
        mode = self._current_mode()
        label = dict((k, t) for t, k in DELETE_MODES)[mode]
        if not show_simulator(self.root, selected, self.stats, label):
            return

        result = delete_files([f.path for f in selected], mode=mode)
        dup_count = sum(1 for f in selected
                        if f.dup_group and not f.is_dup_keeper
                        and f.path in set(result.deleted))
        history_mod.add_record(
            mode=mode,
            files=[{"path": str(f.path), "size": f.size_bytes,
                    "restore_from": result.restore_map.get(str(f.path))}
                   for f in selected if f.path in set(result.deleted)],
            dup_count=dup_count)

        self.settings["delete_mode"] = mode
        save_settings(self.settings)
        self._drop_deleted(set(result.deleted))
        self.status_var.set(result.summary)
        if result.failed:
            detail = "\n".join(f"{p.name}: {why}" for p, why in result.failed[:15])
            messagebox.showwarning("Some files were not deleted", detail)

    def archive_selected(self) -> None:
        selected = self._checked_files()
        if not selected:
            return
        dest = filedialog.asksaveasfilename(
            title="Archive to ZIP", defaultextension=".zip",
            initialfile="cleanup_archive.zip", filetypes=[("ZIP", "*.zip")])
        if not dest:
            return
        if not show_simulator(self.root, selected, self.stats,
                              "Archive to ZIP, then Recycle Bin"):
            return
        try:
            base = Path(os.path.commonpath([str(f.path) for f in selected]))
        except ValueError:
            base = None
        zip_path, result = archive_files([f.path for f in selected],
                                         Path(dest), base_folder=base)
        if zip_path is None:
            messagebox.showerror("Archive failed",
                                 "Could not create the ZIP archive.")
            return
        history_mod.add_record(
            mode="archive",
            files=[{"path": str(p), "size": next(
                (f.size_bytes for f in selected if f.path == p), 0)}
                for p in result.deleted],
            note=f"Archived to {zip_path}")
        self._drop_deleted(set(result.deleted))
        self.status_var.set(f"Archived to {zip_path}. {result.summary}")
        if result.failed:
            detail = "\n".join(f"{p.name}: {why}" for p, why in result.failed[:15])
            messagebox.showwarning("Some files were skipped", detail)

    def undo_last(self) -> None:
        rec = history_mod.last_undoable()
        if not rec:
            messagebox.showinfo(
                "Undo", "No undoable cleanup found.\n\nOnly cleanups made in "
                "Quarantine mode can be undone in-app. Recycle Bin items can "
                "be restored from the OS Recycle Bin; permanent deletions "
                "cannot be undone.")
            return
        mapping = {f["path"]: f["restore_from"] for f in rec.get("files", [])
                   if f.get("restore_from") and not f.get("restored")}
        result = restore_files(mapping)
        history_mod.mark_restored(rec["timestamp"],
                                  {str(p) for p in result.deleted})
        self.status_var.set(f"Restored {len(result.deleted)} file(s) from "
                            f"cleanup {rec['timestamp']}.")
        if result.failed:
            detail = "\n".join(f"{p.name}: {why}" for p, why in result.failed[:15])
            messagebox.showwarning("Some files were not restored", detail)

    def _drop_deleted(self, deleted: set) -> None:
        self.all_files = [f for f in self.all_files if f.path not in deleted]
        self.checked = {id(f) for f in self.all_files if id(f) in self.checked}
        self.dup_groups = [g for g in self.dup_groups
                           if sum(1 for f in g.files if f.path not in deleted) > 1]
        for g in self.dup_groups:
            g.files = [f for f in g.files if f.path not in deleted]
        self._repopulate()
        self._populate_dups()

    def open_organizer(self) -> None:
        files = self._checked_files() or self.all_files
        if not files:
            messagebox.showinfo("Organize", "Run a scan first.")
            return
        OrganizerDialog(self.root, files)

    def toggle_dark(self) -> None:
        self.settings["dark_mode"] = self.dark_var.get()
        save_settings(self.settings)
        self.palette = apply_theme(self.root, self.dark_var.get())
        self._draw_charts()

    def _schedule_tick(self) -> None:
        if scheduler.is_due() and not (self.worker and self.worker.is_alive()):
            self.status_var.set("Running scheduled scan...")
            self.start_scan(silent=True)
        self.root.after(SCHEDULE_POLL_MS, self._schedule_tick)

    def _after_scheduled_scan(self) -> None:
        self.scheduled_run = False
        scheduler.mark_ran()
        note = scheduler.build_notification(
            max(0, self.stats.reclaimable_bytes), self.stats.dup_files)
        if note:
            messagebox.showinfo("Scheduled scan", note)
        elif not scheduler.get_schedule().get("silent", True):
            messagebox.showinfo("Scheduled scan",
                                "Scan complete — nothing urgent found.")


def run() -> None:
    root = tk.Tk()
    FileCleanerApp(root)
    root.mainloop()
