"""Main window for the AI-powered file organization assistant.

Layout:
  toolbar   — folder, cutoff, scan, search, presets, Actions menu
  chips     — active filter chips (click to remove)
  paned     — notebook (Dashboard / Files / Duplicates / Organize /
              Folders / Timeline)  |  collapsible AI chat panel
  footer    — selection tools, delete mode, progress, actions
  status    — status bar

All heavy work runs on background threads feeding a queue; rows render
incrementally so even large scans never freeze the UI.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import history as history_mod
import quick_actions
import scheduler
from ai_analyzer import analyze_files
from analytics import DashboardStats, build_stats
from applog import get_logger
from categorizer import CATEGORY_ICONS, apply_categories, group_by_category
from chat_assistant import ChatAssistant
from config import (DEFAULT_FOLDERS, FILE_CATEGORIES, LARGE_FILE_MB,
                    RECOMMENDATION_LEVELS, STALE_DAYS, ensure_app_dirs,
                    load_settings, save_settings)
from dialogs import (ExclusionsDialog, HistoryWindow, OrganizerDialog,
                     PreviewDialog, SchedulerDialog, show_simulator)
from duplicates import (DuplicateGroup, find_duplicates,
                        find_extended_duplicates, find_similar)
from exclusions import Exclusions
from file_actions import (HAS_TRASH, archive_files, delete_files,
                          restore_files)
from filters import (FilterState, apply_filters, preset_downloads_cleanup,
                     preset_duplicates, preset_large_files)
from folder_health import score_folders
from org_advisor import apply_suggestions, build_suggestions
from recommendations import apply_rules, importance_label
from scanner import FileInfo, human_size, scan_with_stats
from search_engine import search as nl_search
from theme import apply_theme, extend_theme

CHECKED, UNCHECKED = "☑", "☐"
SCHEDULE_POLL_MS = 5 * 60 * 1000
RENDER_CHUNK = 250

DELETE_MODES = [("Recycle Bin (recoverable)", "trash"),
                ("Quarantine (undoable in-app)", "quarantine"),
                ("Permanent (irreversible)", "permanent")]

log = get_logger()


class FileCleanerApp:
    def __init__(self, root: tk.Tk):
        ensure_app_dirs()
        self.root = root
        self.root.title("AI File Cleaner — Organization Assistant")
        self.settings = load_settings()
        self.root.geometry(self.settings.get("window_geometry") or "1320x820")
        self.root.minsize(1000, 600)
        self.palette = apply_theme(root, self.settings.get("dark_mode", False))
        extend_theme(root, self.palette)

        # state
        self.all_files: list[FileInfo] = []
        self.dup_groups: list[DuplicateGroup] = []
        self.ext_dup_groups: list[DuplicateGroup] = []
        self.stats = DashboardStats()
        self.folder_scores = []
        self.suggestions: list[FileInfo] = []
        self.checked: set[int] = set()
        self.item_to_file: dict[str, FileInfo] = {}
        self.dup_item_to_file: dict[str, FileInfo] = {}
        self.org_item_to_file: dict[str, FileInfo] = {}
        self.org_checked: set[int] = set()
        self.filter_state = FilterState()
        self.search_results: list[FileInfo] | None = None
        self.sort_col, self.sort_desc = "size", True
        self.group_var = tk.BooleanVar(
            value=self.settings.get("group_by_category", False))
        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self.custom_folder: Path | None = None
        self.scheduled_run = False
        self.chat = ChatAssistant()
        self._render_job = None

        self._build_menu()
        self._build_toolbar()
        self._build_chips()
        self._build_paned()
        self._build_footer()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._poll_queue)
        self.root.after(4000, self._schedule_tick)

    # ================================================================ chrome

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="Exclusion Rules…",
                          command=lambda: ExclusionsDialog(self.root))
        tools.add_command(label="Scheduled Scans…",
                          command=lambda: SchedulerDialog(self.root))
        tools.add_command(label="Organize Files (manual)…",
                          command=self.open_organizer)
        tools.add_separator()
        tools.add_command(label="Cleanup History…",
                          command=lambda: HistoryWindow(self.root))
        tools.add_command(label="Undo Last Cleanup\tCtrl+Z",
                          command=self.undo_last)
        menubar.add_cascade(label="Tools", menu=tools)

        view = tk.Menu(menubar, tearoff=0)
        self.dark_var = tk.BooleanVar(value=self.settings.get("dark_mode", False))
        view.add_checkbutton(label="Dark Mode\tCtrl+D", variable=self.dark_var,
                             command=self.toggle_dark)
        self.chat_visible = tk.BooleanVar(value=True)
        view.add_checkbutton(label="AI Chat Panel\tCtrl+J",
                             variable=self.chat_visible,
                             command=self._toggle_chat)
        menubar.add_cascade(label="View", menu=view)
        self.root.config(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill="x")

        ttk.Button(bar, text="📁 Choose Folder…",
                   command=self.choose_folder).pack(side="left")
        self.folder_label = ttk.Label(
            bar, text="Default: Downloads, Documents, Desktop",
            style="Muted.TLabel")
        self.folder_label.pack(side="left", padx=8)

        ttk.Label(bar, text="Unused ≥").pack(side="left", padx=(12, 3))
        self.days_var = tk.IntVar(value=STALE_DAYS)
        ttk.Spinbox(bar, from_=1, to=3650, width=5,
                    textvariable=self.days_var).pack(side="left")
        ttk.Label(bar, text="days").pack(side="left", padx=(3, 12))

        self.scan_btn = ttk.Button(bar, text="🔍 Scan  (F5)",
                                   command=self.start_scan)
        self.scan_btn.pack(side="left")
        self.cancel_btn = ttk.Button(bar, text="Cancel (Esc)",
                                     command=self.cancel_scan, state="disabled")
        self.cancel_btn.pack(side="left", padx=4)

        # natural-language search
        ttk.Label(bar, text="🔎").pack(side="left", padx=(16, 2))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=30)
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Return>", lambda e: self._run_search())
        ttk.Button(bar, text="Search", command=self._run_search).pack(
            side="left", padx=3)

        # quick actions
        actions = ttk.Menubutton(bar, text="⚡ Actions")
        amenu = tk.Menu(actions, tearoff=0)
        for label, fn, arg in quick_actions.ALL_ACTIONS:
            amenu.add_command(label=label,
                              command=lambda f=fn, a=arg: self._quick_action(f, a))
        actions["menu"] = amenu
        actions.pack(side="left", padx=8)

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
        presets.pack(side="left")

    def _build_chips(self) -> None:
        self.chips_frame = ttk.Frame(self.root, padding=(10, 0))
        self.chips_frame.pack(fill="x")

    def _refresh_chips(self) -> None:
        for w in self.chips_frame.winfo_children():
            w.destroy()
        chips: list[tuple[str, callable]] = []
        st = self.filter_state
        if self.search_results is not None:
            chips.append((f"search: {self.search_var.get()!r} ✕",
                          self._clear_search))
        for cat in st.categories:
            chips.append((f"category: {cat} ✕", self._reset_filters))
        for lvl in st.levels:
            chips.append((f"rec: {lvl} ✕", self._reset_filters))
        for ext in st.extensions:
            chips.append((f"ext: {ext} ✕", self._reset_filters))
        if st.min_size_mb:
            chips.append((f"≥{st.min_size_mb:g} MB ✕", self._reset_filters))
        if st.duplicates_only:
            chips.append(("duplicates only ✕", self._reset_filters))
        if st.similar_only:
            chips.append(("similar only ✕", self._reset_filters))
        if st.large_only:
            chips.append((f">{LARGE_FILE_MB}MB ✕", self._reset_filters))
        if st.old_over_days:
            chips.append((f"old {st.old_over_days}d+ ✕", self._reset_filters))
        for text, cmd in chips:
            ttk.Button(self.chips_frame, text=text, style="Chip.TButton",
                       command=cmd).pack(side="left", padx=2, pady=2)

    def _build_paned(self) -> None:
        self.paned = ttk.PanedWindow(self.root, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=8, pady=(2, 4))

        left = ttk.Frame(self.paned)
        self.paned.add(left, weight=4)
        self.notebook = ttk.Notebook(left)
        self.notebook.pack(fill="both", expand=True)
        self._build_dashboard_tab()
        self._build_files_tab()
        self._build_dups_tab()
        self._build_org_tab()
        self._build_folders_tab()
        self._build_timeline_tab()
        self.notebook.select(1)

        self._build_chat_panel()

    # ---------------------------------------------------------- dashboard

    def _build_dashboard_tab(self) -> None:
        outer = ttk.Frame(self.notebook)
        self.notebook.add(outer, text="  📊 Dashboard  ")
        canvas = tk.Canvas(outer, highlightthickness=0,
                           bg=self.palette["bg"])
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.dash = ttk.Frame(canvas, padding=10)
        self.dash_window = canvas.create_window((0, 0), window=self.dash,
                                                anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.dash_canvas = canvas
        self.dash.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(
            self.dash_window, width=e.width))

        self.cards_frame = ttk.Frame(self.dash)
        self.cards_frame.pack(fill="x")
        self.types_frame = ttk.Frame(self.dash)
        self.types_frame.pack(fill="x", pady=(6, 0))
        mid = ttk.Frame(self.dash)
        mid.pack(fill="x", pady=(10, 0))
        mid.columnconfigure(0, weight=3)
        mid.columnconfigure(1, weight=2)
        self.chart = tk.Canvas(mid, highlightthickness=0, height=340,
                               bg=self.palette["surface"])
        self.chart.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ttk.Frame(mid)
        right.grid(row=0, column=1, sticky="nsew")
        self.top_list = ttk.Treeview(right, columns=("name", "size"),
                                     show="headings", height=6)
        self.top_list.heading("name", text="Top 10 largest files")
        self.top_list.heading("size", text="Size")
        self.top_list.column("name", width=250)
        self.top_list.column("size", width=90, anchor="e")
        self.top_list.pack(fill="both", expand=True)
        self.extremes_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.extremes_var, style="Muted.TLabel",
                  wraplength=380, justify="left").pack(fill="x", pady=(6, 0))
        self._render_dashboard()

    def _card(self, parent, icon: str, title: str, value: str,
              col: int, row: int, small: bool = False) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(10, 6))
        card.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
        parent.columnconfigure(col, weight=1)
        ttk.Label(card, text=f"{icon} {title}",
                  style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, text=value, style="CardValue.TLabel",
                  wraplength=200).pack(anchor="w")

    def _render_dashboard(self) -> None:
        for frame in (self.cards_frame, self.types_frame):
            for w in frame.winfo_children():
                w.destroy()
        s = self.stats
        cards = [
            ("🗃", "Files scanned", f"{s.total_files:,}"),
            ("📂", "Folders scanned", f"{s.total_dirs:,}"),
            ("💾", "Storage scanned", human_size(s.total_bytes)),
            ("🕰", "Unused 30+ days", f"{s.unused_30:,}"),
            ("🕰", "Unused 90+ days", f"{s.unused_90:,}"),
            ("🕰", "Unused 1 year+", f"{s.unused_365:,}"),
            ("🧹", "Est. cleanup size", human_size(max(0, s.reclaimable_bytes))),
            ("♊", "Duplicate files", f"{s.dup_files:,}"),
            ("🗑", "Wasted by duplicates", human_size(s.dup_wasted_bytes)),
            ("🫙", "Empty folders", f"{len(s.empty_dirs):,}"),
            ("📄", "Largest file",
             f"{s.largest_file[0]}\n{human_size(s.largest_file[1])}"),
            ("📁", "Largest folder",
             f"{Path(s.largest_folder[0]).name or s.largest_folder[0]}"
             f"\n{human_size(s.largest_folder[1])}"),
        ]
        for i, (icon, title, value) in enumerate(cards):
            self._card(self.cards_frame, icon, title, value,
                       col=i % 4, row=i // 4)

        icons = {"Images": "🖼", "PDFs": "📕", "Word docs": "📝",
                 "Excel files": "📊", "Videos": "🎬", "Audio": "🎵",
                 "Code files": "💻", "Archives": "📦"}
        col = 0
        for label, (count, size) in s.type_counts.items():
            self._card(self.types_frame, icons.get(label, "📄"), label,
                       f"{count:,} · {human_size(size)}", col=col % 5,
                       row=col // 5, small=True)
            col += 1
        self._card(self.types_frame, "❓", "Unknown", f"{s.unknown_count:,}",
                   col=col % 5, row=col // 5, small=True)

        self.top_list.delete(*self.top_list.get_children())
        for name, size in s.top_files:
            self.top_list.insert("", "end", values=(name, human_size(size)))

        extremes = []
        if s.oldest_files:
            extremes.append("Oldest: " + ", ".join(
                f"{Path(p).name} ({datetime.fromtimestamp(m).year})"
                for p, m in s.oldest_files[:3]))
        if s.newest_files:
            extremes.append("Newest: " + ", ".join(
                Path(p).name for p, m in s.newest_files[:3]))
        if s.most_duplicated_folders:
            extremes.append("Most duplicated folders: " + ", ".join(
                f"{Path(f).name} ({n})" for f, n in s.most_duplicated_folders[:3]))
        self.extremes_var.set("\n".join(extremes))
        self.root.after(50, self._draw_charts)

    def _draw_charts(self) -> None:
        c = self.chart
        c.delete("all")
        c.configure(bg=self.palette["surface"])
        w = max(c.winfo_width(), 300)
        h = max(int(c["height"]), 220)
        data = self.stats.ext_breakdown[:8]
        cats = self.stats.cat_breakdown[:6]
        if not data:
            c.create_text(w // 2, h // 2, text="Run a scan to see storage charts",
                          fill=self.palette["muted"], font=("Segoe UI", 11))
            return
        colors = self.palette["chart"]
        half = h // 2
        self._bars(c, data, 0, 0, w, half, "Storage by extension", colors,
                   fmt=lambda t: f"{t[0]} · {human_size(t[1])} · {t[2]} files",
                   value=lambda t: t[1])
        self._bars(c, cats, 0, half, w, half, "Storage by category", colors,
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
            length = int((w - 240) * value(row) / maxval)
            c.create_rectangle(x + 10, top, x + 10 + max(length, 2),
                               top + bar_h, fill=colors[i % len(colors)],
                               width=0)
            c.create_text(x + 16 + max(length, 2), top + bar_h // 2,
                          anchor="w", text=fmt(row),
                          fill=self.palette["muted"], font=("Segoe UI", 9))

    # ---------------------------------------------------------- files tab

    def _build_files_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(4, 4))
        self.notebook.add(tab, text="  🗂 Files  ")

        panel = ttk.Frame(tab, padding=(4, 2))
        panel.pack(fill="x")
        ttk.Checkbutton(panel, text="Group by category",
                        variable=self.group_var,
                        command=self._repopulate).pack(side="left", padx=(0, 10))
        ttk.Label(panel, text="Category").pack(side="left")
        self.f_cat = ttk.Combobox(panel, state="readonly", width=11,
                                  values=["All"] + FILE_CATEGORIES)
        self.f_cat.set("All")
        self.f_cat.pack(side="left", padx=(2, 8))
        ttk.Label(panel, text="Recommendation").pack(side="left")
        self.f_rec = ttk.Combobox(panel, state="readonly", width=17,
                                  values=["All"] + RECOMMENDATION_LEVELS)
        self.f_rec.set("All")
        self.f_rec.pack(side="left", padx=(2, 8))
        ttk.Label(panel, text="Ext").pack(side="left")
        self.f_ext = ttk.Entry(panel, width=7)
        self.f_ext.pack(side="left", padx=(2, 8))
        ttk.Label(panel, text="Min MB").pack(side="left")
        self.f_min = ttk.Entry(panel, width=5)
        self.f_min.pack(side="left", padx=(2, 8))
        self.f_dup = tk.BooleanVar()
        self.f_sim = tk.BooleanVar()
        self.f_large = tk.BooleanVar()
        self.f_old = tk.BooleanVar()
        for text, var in [("Dups", self.f_dup), ("Similar", self.f_sim),
                          (f">{LARGE_FILE_MB}MB", self.f_large),
                          ("Old 1y+", self.f_old)]:
            ttk.Checkbutton(panel, text=text, variable=var).pack(side="left", padx=2)
        ttk.Button(panel, text="Apply",
                   command=self._apply_filter_panel).pack(side="left", padx=6)
        ttk.Button(panel, text="Reset",
                   command=self._reset_filters).pack(side="left")

        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        cols = ("check", "name", "size", "accessed", "idle", "stars",
                "health", "rec", "explanation")
        self.tree = ttk.Treeview(frame, columns=cols, show="tree headings",
                                 selectmode="extended")
        self.tree.column("#0", width=0, stretch=False)
        headings = {
            "check": ("", 34), "name": ("File Name", 190),
            "size": ("Size", 75), "accessed": ("Accessed", 95),
            "idle": ("Idle (d)", 58), "stars": ("Importance", 90),
            "health": ("Health", 55), "rec": ("Recommendation", 135),
            "explanation": ("AI Explanation (with reasons)", 360),
        }
        for col, (title, width) in headings.items():
            self.tree.heading(col, text=title,
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=width,
                             stretch=col in ("name", "explanation"),
                             anchor="center" if col in ("check", "size", "idle",
                                                        "health", "stars") else "w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("crit", foreground=self.palette["danger"])
        self.tree.tag_configure("group", font=("Segoe UI", 9, "bold"))

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Button-2>", self._on_right_click)

        self.detail_var = tk.StringVar(
            value="Select a row for full path, reasoning, and health factors.")
        ttk.Label(tab, textvariable=self.detail_var, padding=(8, 4),
                  wraplength=1150, style="Muted.TLabel").pack(fill="x")

        self.ctx_menu = tk.Menu(self.root, tearoff=0)
        self.ctx_menu.add_command(label="Open file", command=self._ctx_open)
        self.ctx_menu.add_command(label="Open containing folder",
                                  command=self._ctx_open_folder)
        self.ctx_menu.add_command(label="Toggle checkbox",
                                  command=self._ctx_toggle)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Exclude this folder from scans",
                                  command=self._ctx_exclude_folder)

    # ---------------------------------------------------------- dups tab

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

        cols = ("check", "level", "size", "modified", "role", "path")
        self.dup_tree = ttk.Treeview(tab, columns=cols, show="tree headings")
        specs = [("#0", "Group", 210), ("check", "", 34),
                 ("level", "Level", 120), ("size", "Size", 75),
                 ("modified", "Modified", 95), ("role", "Role", 80),
                 ("path", "Path", 420)]
        for col, title, width in specs:
            self.dup_tree.heading(col, text=title)
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
        all_groups = self.dup_groups + self.ext_dup_groups
        wasted = sum(g.wasted_bytes for g in self.dup_groups)
        self.dup_summary.config(
            text=f"{len(self.dup_groups)} exact group(s) "
                 f"({human_size(wasted)} wasted) + "
                 f"{len(self.ext_dup_groups)} name-based group(s).")
        for g in all_groups:
            parent = self.dup_tree.insert(
                "", "end",
                text=f"Group #{g.group_id} — {len(g.files)} file(s) — "
                     f"{human_size(g.wasted_bytes)} extra", open=False)
            for f in sorted(g.files, key=lambda x: x.last_modified):
                role = "Original" if f.is_dup_keeper else "Duplicate"
                item = self.dup_tree.insert(parent, "end", values=(
                    CHECKED if id(f) in self.checked else UNCHECKED,
                    g.level, f.size_human,
                    f.last_modified.strftime("%Y-%m-%d"), role, str(f.path)))
                self.dup_item_to_file[item] = f

    def _on_dup_click(self, event) -> None:
        if self.dup_tree.identify_column(event.x) != "#1":
            return
        item = self.dup_tree.identify_row(event.y)
        f = self.dup_item_to_file.get(item)
        if f:
            self._toggle_file(f)

    def _check_all_dups(self) -> None:
        for g in self.dup_groups:      # exact groups only — safe default
            for f in g.files:
                if not f.is_dup_keeper:
                    self.checked.add(id(f))
        self._refresh_checks()

    # ---------------------------------------------------------- organize tab

    def _build_org_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(tab, text="  🧭 Organize  ")
        top = ttk.Frame(tab)
        top.pack(fill="x", pady=(0, 4))
        self.org_summary = ttk.Label(
            top, text="Run a scan to get organization recommendations.",
            style="Muted.TLabel")
        self.org_summary.pack(side="left")
        ttk.Button(top, text="Select all ≥80%",
                   command=self._org_select_confident).pack(side="right")

        cols = ("check", "name", "current", "suggested", "conf")
        self.org_tree = ttk.Treeview(tab, columns=cols, show="headings")
        specs = [("check", "", 34), ("name", "File", 190),
                 ("current", "Current Location", 280),
                 ("suggested", "Suggested Folder", 200),
                 ("conf", "Confidence", 90)]
        for col, title, width in specs:
            self.org_tree.heading(col, text=title)
            self.org_tree.column(col, width=width,
                                 stretch=col in ("current", "suggested"),
                                 anchor="center" if col in ("check", "conf") else "w")
        vsb = ttk.Scrollbar(tab, orient="vertical", command=self.org_tree.yview)
        self.org_tree.configure(yscrollcommand=vsb.set)
        self.org_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for tag, color in [("hi", self.palette["ok"]),
                           ("mid", self.palette["warn"]),
                           ("lo", self.palette["muted"])]:
            self.org_tree.tag_configure(tag, foreground=color)
        self.org_tree.bind("<Button-1>", self._on_org_click)
        self.org_tree.bind("<<TreeviewSelect>>", self._on_org_select)

        bottom = ttk.Frame(tab)
        bottom.pack(fill="x", side="bottom", pady=4)
        self.org_detail = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.org_detail, style="Muted.TLabel",
                  wraplength=1100, padding=(6, 2)).pack(fill="x", side="bottom")
        ttk.Button(bottom, text="Preview Changes",
                   command=self._org_preview).pack(side="left")
        ttk.Button(bottom, text="Apply Changes",
                   command=self._org_apply).pack(side="left", padx=6)
        ttk.Button(bottom, text="Undo (last move batch)",
                   command=self.undo_last).pack(side="left")

    def _populate_org(self) -> None:
        self.org_tree.delete(*self.org_tree.get_children())
        self.org_item_to_file.clear()
        self.org_checked.clear()
        for f in self.suggestions:
            tag = "hi" if f.confidence >= 80 else ("mid" if f.confidence >= 60
                                                   else "lo")
            item = self.org_tree.insert("", "end", tags=(tag,), values=(
                UNCHECKED, f.name, str(f.path.parent),
                f.suggested_folder, f"{f.confidence}%"))
            self.org_item_to_file[item] = f
        self.org_summary.config(
            text=f"{len(self.suggestions)} recommendation(s). Green = high "
                 "confidence. Nothing moves until you press Apply.")

    def _on_org_click(self, event) -> None:
        if self.org_tree.identify_column(event.x) != "#1":
            return
        item = self.org_tree.identify_row(event.y)
        f = self.org_item_to_file.get(item)
        if not f:
            return
        if id(f) in self.org_checked:
            self.org_checked.discard(id(f))
            self.org_tree.set(item, "check", UNCHECKED)
        else:
            self.org_checked.add(id(f))
            self.org_tree.set(item, "check", CHECKED)

    def _on_org_select(self, _event) -> None:
        sel = self.org_tree.selection()
        f = self.org_item_to_file.get(sel[0]) if sel else None
        if f:
            self.org_detail.set(
                f"{f.path}  →  ~/{f.suggested_folder}/   |   Why: "
                f"{f.suggest_reason}   |   Confidence {f.confidence}%")

    def _org_select_confident(self) -> None:
        for item, f in self.org_item_to_file.items():
            if f.confidence >= 80:
                self.org_checked.add(id(f))
                self.org_tree.set(item, "check", CHECKED)

    def _org_selected(self) -> list[FileInfo]:
        return [f for f in self.suggestions if id(f) in self.org_checked]

    def _org_preview(self) -> None:
        sel = self._org_selected()
        if not sel:
            messagebox.showinfo("Preview", "Check some recommendations first.")
            return
        rows = [(f.name, str(f.path.parent),
                 f"~/{f.suggested_folder}", f"{f.confidence}%") for f in sel]
        PreviewDialog(self.root, "Preview organization changes",
                      f"{len(sel)} file(s) will move. Review and confirm:",
                      [("name", "File", 160), ("cur", "From", 250),
                       ("dst", "To", 200), ("conf", "Confidence", 80)],
                      rows, on_confirm=lambda: self._org_apply(confirmed=True))

    def _org_apply(self, confirmed: bool = False) -> None:
        sel = self._org_selected()
        if not sel:
            messagebox.showinfo("Apply", "Check some recommendations first.")
            return
        if not confirmed and not messagebox.askyesno(
                "Apply changes", f"Move {len(sel)} file(s) to their "
                "suggested folders under your home directory?"):
            return
        moved, failed = apply_suggestions(sel)
        if moved:
            history_mod.add_record(mode="move", files=moved,
                                   note="AI organization")
            log.info("Organized %d file(s); %d failed", len(moved), len(failed))
        self.suggestions = [f for f in self.suggestions
                            if id(f) not in self.org_checked or
                            any(m["path"] == str(f.path) for m in moved)]
        self.suggestions = build_suggestions(self.all_files)
        self._populate_org()
        self._repopulate()
        self.status_var.set(f"Moved {len(moved)} file(s)."
                            + (f" {len(failed)} failed." if failed else "")
                            + " Undo is available in Tools.")
        if failed:
            messagebox.showwarning("Some files were not moved", "\n".join(
                f"{p}: {why}" for p, why in failed[:12]))

    # ---------------------------------------------------------- folders tab

    def _build_folders_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(tab, text="  🩺 Folders  ")
        cols = ("score", "folder", "reasons", "rec")
        self.folder_tree = ttk.Treeview(tab, columns=cols, show="headings")
        specs = [("score", "Health", 70), ("folder", "Folder", 260),
                 ("reasons", "Findings", 380),
                 ("rec", "Recommendation", 280)]
        for col, title, width in specs:
            self.folder_tree.heading(col, text=title)
            self.folder_tree.column(col, width=width,
                                    stretch=col in ("reasons", "rec"),
                                    anchor="center" if col == "score" else "w")
        vsb = ttk.Scrollbar(tab, orient="vertical",
                            command=self.folder_tree.yview)
        self.folder_tree.configure(yscrollcommand=vsb.set)
        self.folder_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for tag, color in [("bad", self.palette["danger"]),
                           ("warn", self.palette["warn"]),
                           ("good", self.palette["ok"])]:
            self.folder_tree.tag_configure(tag, foreground=color)

    def _populate_folders(self) -> None:
        self.folder_tree.delete(*self.folder_tree.get_children())
        for s in self.folder_scores[:300]:
            tag = "bad" if s.score < 50 else ("warn" if s.score < 80 else "good")
            self.folder_tree.insert("", "end", tags=(tag,), values=(
                f"{s.score}/100", s.folder, "; ".join(s.reasons),
                s.recommendation))

    # ---------------------------------------------------------- timeline tab

    def _build_timeline_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(tab, text="  📅 Timeline  ")
        cols = ("size", "modified", "path")
        self.time_tree = ttk.Treeview(tab, columns=cols, show="tree headings")
        self.time_tree.heading("#0", text="Year / Month / File")
        self.time_tree.column("#0", width=280)
        for col, title, width in [("size", "Size", 90),
                                  ("modified", "Modified", 110),
                                  ("path", "Path", 460)]:
            self.time_tree.heading(col, text=title)
            self.time_tree.column(col, width=width, stretch=(col == "path"))
        vsb = ttk.Scrollbar(tab, orient="vertical",
                            command=self.time_tree.yview)
        self.time_tree.configure(yscrollcommand=vsb.set)
        self.time_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def _populate_timeline(self) -> None:
        self.time_tree.delete(*self.time_tree.get_children())
        by_year: dict[int, dict[str, list[FileInfo]]] = {}
        for f in self.all_files:
            y = f.last_modified.year
            m = f.last_modified.strftime("%B")
            by_year.setdefault(y, {}).setdefault(m, []).append(f)
        for y in sorted(by_year, reverse=True):
            ynode = self.time_tree.insert(
                "", "end", text=str(y), open=False,
                values=("", "", f"{sum(len(v) for v in by_year[y].values())} file(s)"))
            months = by_year[y]
            for m in sorted(months,
                            key=lambda mm: datetime.strptime(mm, "%B").month,
                            reverse=True):
                mnode = self.time_tree.insert(
                    ynode, "end", text=m, open=False,
                    values=("", "", f"{len(months[m])} file(s)"))
                for f in sorted(months[m], key=lambda x: x.last_modified,
                                reverse=True)[:200]:
                    self.time_tree.insert(mnode, "end", text=f.name, values=(
                        f.size_human, f.last_modified.strftime("%Y-%m-%d"),
                        str(f.path)))

    # ---------------------------------------------------------- chat panel

    def _build_chat_panel(self) -> None:
        self.chat_frame = ttk.Frame(self.paned, style="Chat.TFrame", padding=6)
        self.paned.add(self.chat_frame, weight=1)
        ttk.Label(self.chat_frame, text="🤖 AI Assistant",
                  style="Card.TLabel",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(self.chat_frame,
                  text="Ask about your scan: 'what can I safely delete?', "
                       "'show duplicate resumes', 'largest videos'…",
                  style="CardTitle.TLabel", wraplength=250,
                  justify="left").pack(anchor="w", pady=(0, 4))
        self.chat_log = tk.Text(self.chat_frame, width=34, wrap="word",
                                state="disabled", relief="flat",
                                bg=self.palette["surface"],
                                fg=self.palette["text"],
                                font=("Segoe UI", 9))
        self.chat_log.pack(fill="both", expand=True)
        entry_row = ttk.Frame(self.chat_frame, style="Chat.TFrame")
        entry_row.pack(fill="x", pady=(4, 0))
        self.chat_var = tk.StringVar()
        chat_entry = ttk.Entry(entry_row, textvariable=self.chat_var)
        chat_entry.pack(side="left", fill="x", expand=True)
        chat_entry.bind("<Return>", lambda e: self._chat_send())
        ttk.Button(entry_row, text="Ask",
                   command=self._chat_send).pack(side="right", padx=(4, 0))

    def _toggle_chat(self) -> None:
        if self.chat_visible.get():
            self.paned.add(self.chat_frame, weight=1)
        else:
            self.paned.forget(self.chat_frame)

    def _chat_send(self) -> None:
        q = self.chat_var.get().strip()
        if not q:
            return
        self.chat_var.set("")
        self._chat_append(f"You: {q}\n", user=True)
        threading.Thread(target=lambda: self.msg_queue.put(
            ("chat", self.chat.answer(q))), daemon=True).start()

    def _chat_append(self, text: str, user: bool = False) -> None:
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", text + ("\n" if not user else ""))
        self.chat_log.see("end")
        self.chat_log.configure(state="disabled")

    # ---------------------------------------------------------- footer

    def _build_footer(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Select All",
                   command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(bar, text="Select None",
                   command=lambda: self._set_all(False)).pack(side="left", padx=4)
        ttk.Label(bar, text="Delete mode").pack(side="left", padx=(14, 4))
        self.mode_box = ttk.Combobox(bar, state="readonly", width=26,
                                     values=[m[0] for m in DELETE_MODES])
        default_mode = self.settings.get("delete_mode", "trash")
        self.mode_box.set(next((label for label, key in DELETE_MODES
                                if key == default_mode), DELETE_MODES[0][0]))
        self.mode_box.pack(side="left")
        if not HAS_TRASH:
            self.mode_box.set(DELETE_MODES[1][0])
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=150)
        self.progress.pack(side="left", padx=14)
        self.delete_btn = ttk.Button(bar, text="🗑 Delete Selected (0)",
                                     style="Danger.TButton",
                                     command=self.delete_selected,
                                     state="disabled")
        self.delete_btn.pack(side="right")
        self.archive_btn = ttk.Button(bar, text="📦 Archive Selected",
                                      command=self.archive_selected,
                                      state="disabled")
        self.archive_btn.pack(side="right", padx=6)
        ttk.Button(bar, text="↩ Undo Last",
                   command=self.undo_last).pack(side="right", padx=6)
        self.status_var = tk.StringVar(value="Ready. Press F5 or Scan.")
        ttk.Label(self.root, textvariable=self.status_var,
                  style="Status.TLabel", anchor="w").pack(fill="x",
                                                          side="bottom")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<F5>", lambda e: self.start_scan())
        self.root.bind("<Escape>", lambda e: self.cancel_scan())
        self.root.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.root.bind("<Control-d>", lambda e: self._kbd_dark())
        self.root.bind("<Control-j>", lambda e: self._kbd_chat())
        self.root.bind("<Control-z>", lambda e: self.undo_last())
        self.root.bind("<Delete>", lambda e: self.delete_selected())
        self.root.bind("<Control-a>", self._kbd_select_all)

    def _kbd_dark(self) -> None:
        self.dark_var.set(not self.dark_var.get())
        self.toggle_dark()

    def _kbd_chat(self) -> None:
        self.chat_visible.set(not self.chat_visible.get())
        self._toggle_chat()

    def _kbd_select_all(self, event) -> str:
        if isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text)):
            return ""
        self._set_all(True)
        return "break"

    def _on_close(self) -> None:
        self.settings["window_geometry"] = self.root.geometry()
        self.settings["group_by_category"] = self.group_var.get()
        save_settings(self.settings)
        self.root.destroy()

    # ================================================================ scanning

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
        self.search_results = None
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
        """Background pipeline: scan → dups → similar → rules → categorize
        → suggestions → folder scores → stats → AI analysis."""
        put = self.msg_queue.put
        try:
            rules = Exclusions.load()
            stale, raw = scan_with_stats(
                folders, min_age_days=days,
                progress=lambda m: put(("status", m)),
                cancel_check=self.cancel_flag.is_set,
                is_excluded=rules.is_excluded)
            put(("status", "Detecting duplicates (5 levels)..."))
            dup_groups = find_duplicates(
                stale, progress=lambda m: put(("status", m)),
                cancel_check=self.cancel_flag.is_set)
            ext_groups = find_extended_duplicates(stale, dup_groups)
            find_similar(stale)
            put(("status", "Classifying, scoring, and building suggestions..."))
            apply_rules(stale)
            apply_categories(stale)
            suggestions = build_suggestions(stale)
            scores = score_folders(stale, raw)
            stats = build_stats(stale, raw, dup_groups)
            put(("results", (stale, dup_groups, ext_groups, stats,
                             scores, suggestions)))

            if stale and not self.cancel_flag.is_set():
                analyze_files(stale,
                              progress=lambda m: put(("status", m)),
                              cancel_check=self.cancel_flag.is_set)
                put(("refresh", None))
            put(("done", f"Done. {len(stale)} stale file(s), "
                         f"{len(dup_groups)} exact duplicate group(s), "
                         f"{len(suggestions)} organization suggestion(s)."))
        except Exception as exc:
            log.exception("Scan failed")
            put(("done", f"Scan failed: {exc}"))

    # ================================================================ queue

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload)[:180])
                elif kind == "results":
                    (self.all_files, self.dup_groups, self.ext_dup_groups,
                     self.stats, self.folder_scores, self.suggestions) = payload
                    self.chat.update_context(self.all_files, self.stats,
                                             self.folder_scores)
                    self._repopulate()
                    self._populate_dups()
                    self._populate_org()
                    self._populate_folders()
                    self._populate_timeline()
                    self._render_dashboard()
                elif kind == "refresh":
                    self._repopulate()
                elif kind == "chat":
                    self._chat_append(f"Assistant: {payload}")
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

    # ================================================================ table

    def _visible_files(self) -> list[FileInfo]:
        base = (self.search_results if self.search_results is not None
                else apply_filters(self.all_files, self.filter_state))
        keymap = {
            "name": lambda f: f.name.lower(), "size": lambda f: f.size_bytes,
            "accessed": lambda f: f.last_accessed,
            "idle": lambda f: f.days_idle, "health": lambda f: f.health,
            "stars": lambda f: f.importance,
            "rec": lambda f: f.recommendation,
            "explanation": lambda f: f.explanation.lower(),
            "check": lambda f: id(f) in self.checked,
        }
        key = keymap.get(self.sort_col, keymap["size"])
        return sorted(base, key=key, reverse=self.sort_desc)

    def _repopulate(self) -> None:
        if self._render_job:
            self.root.after_cancel(self._render_job)
            self._render_job = None
        self.tree.delete(*self.tree.get_children())
        self.item_to_file.clear()
        files = self._visible_files()
        self._refresh_chips()

        if self.group_var.get():
            groups = group_by_category(files)
            pending: list[tuple[str, FileInfo]] = []
            for cat, members in groups.items():
                size = human_size(sum(f.size_bytes for f in members))
                node = self.tree.insert(
                    "", "end", open=True, tags=("group",),
                    text=f"{CATEGORY_ICONS.get(cat, '')} {cat} "
                         f"({len(members)} · {size})")
                pending.extend((node, f) for f in members)
            self.tree.column("#0", width=220, stretch=False)
        else:
            self.tree.column("#0", width=0, stretch=False)
            pending = [("", f) for f in files]

        self._render_rows(pending, 0)
        shown = len(pending)
        if shown != len(self.all_files):
            self.status_var.set(
                f"Showing {shown} of {len(self.all_files)} stale files.")

    def _render_rows(self, pending: list, start: int) -> None:
        """Incremental (lazy) rendering so huge lists never block the UI."""
        end = min(start + RENDER_CHUNK, len(pending))
        for parent, f in pending[start:end]:
            tags = ("crit",) if f.importance >= 5 else ()
            item = self.tree.insert(parent, "end", tags=tags,
                                    values=self._row_values(f))
            self.item_to_file[item] = f
        if end < len(pending):
            self._render_job = self.root.after(
                15, lambda: self._render_rows(pending, end))
        else:
            self._render_job = None
            self._update_buttons()

    def _row_values(self, f: FileInfo) -> tuple:
        return (CHECKED if id(f) in self.checked else UNCHECKED, f.name,
                f.size_human, f.last_accessed.strftime("%Y-%m-%d"),
                f.days_idle, f.stars, f.health, f.recommendation,
                f.explanation)

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

    # -- search & filters

    def _run_search(self) -> None:
        q = self.search_var.get().strip()
        if not q:
            self._clear_search()
            return
        results, desc = nl_search(self.all_files, q)
        self.search_results = results
        self.status_var.set(desc)
        self.notebook.select(1)
        self._repopulate()

    def _clear_search(self) -> None:
        self.search_results = None
        self.search_var.set("")
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
        self.search_results = None
        self._repopulate()

    def _apply_preset(self, preset: FilterState) -> None:
        self.filter_state = preset
        self.search_results = None
        self._repopulate()
        self.notebook.select(1)

    def _reset_filters(self) -> None:
        self.filter_state = FilterState()
        self.search_results = None
        self.search_var.set("")
        self.f_cat.set("All")
        self.f_rec.set("All")
        self.f_ext.delete(0, "end")
        self.f_min.delete(0, "end")
        for var in (self.f_dup, self.f_sim, self.f_large, self.f_old):
            var.set(False)
        self._repopulate()

    # -- row interaction

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
                f"{f.path}   |   {importance_label(f.importance)} "
                f"({f.stars}): {f.importance_reason}   |   Why: {f.rec_reason}"
                f"   |   Health {f.health}: {f.health_reason}")

    def _on_right_click(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if item and item in self.item_to_file:
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

    # ================================================================ actions

    def _current_mode(self) -> str:
        label = self.mode_box.get()
        return next((key for text, key in DELETE_MODES if text == label),
                    "trash")

    def delete_selected(self) -> None:
        self._execute_delete(self._checked_files())

    def _execute_delete(self, selected: list[FileInfo]) -> None:
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
        log.info("Deleted %d file(s) via %s; %d failed",
                 len(result.deleted), mode, len(result.failed))
        self.settings["delete_mode"] = mode
        save_settings(self.settings)
        self._drop_deleted(set(result.deleted))
        self.status_var.set(result.summary)
        if result.failed:
            messagebox.showwarning("Some files were not deleted", "\n".join(
                f"{p.name}: {why}" for p, why in result.failed[:15]))

    def archive_selected(self, selected: list[FileInfo] = None) -> None:
        selected = selected or self._checked_files()
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
        log.info("Archived %d file(s) to %s", len(result.deleted), zip_path)
        self._drop_deleted(set(result.deleted))
        self.status_var.set(f"Archived to {zip_path}. {result.summary}")
        if result.failed:
            messagebox.showwarning("Some files were skipped", "\n".join(
                f"{p.name}: {why}" for p, why in result.failed[:15]))

    def undo_last(self) -> None:
        rec = history_mod.last_undoable()
        if not rec:
            messagebox.showinfo(
                "Undo", "No undoable cleanup found.\n\nQuarantine deletions "
                "and organization moves can be undone in-app. Recycle Bin "
                "items can be restored from the OS Recycle Bin; permanent "
                "deletions cannot be undone.")
            return
        mapping = {f["path"]: f["restore_from"] for f in rec.get("files", [])
                   if f.get("restore_from") and not f.get("restored")}
        result = restore_files(mapping)
        history_mod.mark_restored(rec["timestamp"],
                                  {str(p) for p in result.deleted})
        log.info("Restored %d file(s) from %s", len(result.deleted),
                 rec["timestamp"])
        self.status_var.set(f"Restored {len(result.deleted)} file(s) from "
                            f"{rec['mode']} batch {rec['timestamp']}.")
        if result.failed:
            messagebox.showwarning("Some files were not restored", "\n".join(
                f"{p.name}: {why}" for p, why in result.failed[:15]))

    def _drop_deleted(self, deleted: set) -> None:
        self.all_files = [f for f in self.all_files if f.path not in deleted]
        self.checked = {id(f) for f in self.all_files if id(f) in self.checked}
        for groups in (self.dup_groups, self.ext_dup_groups):
            for g in groups:
                g.files = [f for f in g.files if f.path not in deleted]
        self.dup_groups = [g for g in self.dup_groups if len(g.files) > 1]
        self.ext_dup_groups = [g for g in self.ext_dup_groups
                               if len(g.files) > 1]
        if self.search_results is not None:
            self.search_results = [f for f in self.search_results
                                   if f.path not in deleted]
        self._repopulate()
        self._populate_dups()

    # ---------------------------------------------------------- quick actions

    def _quick_action(self, fn, arg_kind: str) -> None:
        if not self.all_files and not self.stats.total_files:
            messagebox.showinfo("Actions", "Run a scan first.")
            return
        plan = fn(self.stats) if arg_kind == "stats" else fn(self.all_files)
        if not plan.files and not plan.dirs:
            messagebox.showinfo(plan.name, "Nothing matched this action.")
            return
        if plan.kind == "rmdir":
            rows = [(d,) for d in plan.dirs]
            cols = [("dir", "Empty folder", 600)]
        elif plan.kind == "organize":
            rows = [(f.name, str(f.path.parent), f"~/{f.suggested_folder}")
                    for f in plan.files]
            cols = [("name", "File", 180), ("cur", "From", 260),
                    ("dst", "To", 200)]
        else:
            rows = [(f.name, f.size_human, f"{f.days_idle}d",
                     str(f.path.parent)) for f in plan.files]
            cols = [("name", "File", 200), ("size", "Size", 80),
                    ("idle", "Idle", 60), ("folder", "Folder", 300)]
        PreviewDialog(self.root, plan.name, plan.description, cols, rows,
                      on_confirm=lambda: self._execute_plan(plan))

    def _execute_plan(self, plan) -> None:
        if plan.kind == "rmdir":
            removed, failed = quick_actions.remove_empty_dirs(plan.dirs)
            self.stats.empty_dirs = [d for d in self.stats.empty_dirs
                                     if d not in set(plan.dirs) - {f for f, _ in failed}]
            log.info("Removed %d empty folder(s)", removed)
            self.status_var.set(f"Removed {removed} empty folder(s)."
                                + (f" {len(failed)} failed." if failed else ""))
            self._render_dashboard()
        elif plan.kind == "delete":
            self._execute_delete(plan.files)
        elif plan.kind == "archive":
            self.archive_selected(plan.files)
        elif plan.kind == "organize":
            moved, failed = apply_suggestions(plan.files)
            if moved:
                history_mod.add_record(mode="move", files=moved,
                                       note=plan.name)
                log.info("%s: moved %d file(s)", plan.name, len(moved))
            self.suggestions = build_suggestions(self.all_files)
            self._populate_org()
            self._repopulate()
            self.status_var.set(f"{plan.name}: moved {len(moved)} file(s)."
                                + (f" {len(failed)} failed." if failed else ""))

    def open_organizer(self) -> None:
        files = self._checked_files() or self.all_files
        if not files:
            messagebox.showinfo("Organize", "Run a scan first.")
            return
        OrganizerDialog(self.root, files)

    # ================================================================ theme

    def toggle_dark(self) -> None:
        self.settings["dark_mode"] = self.dark_var.get()
        save_settings(self.settings)
        self.palette = apply_theme(self.root, self.dark_var.get())
        extend_theme(self.root, self.palette)
        self.chat_log.configure(bg=self.palette["surface"],
                                fg=self.palette["text"])
        self.dash_canvas.configure(bg=self.palette["bg"])
        self._draw_charts()

    # ================================================================ scheduler

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
