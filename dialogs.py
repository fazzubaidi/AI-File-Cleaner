"""Secondary windows: exclusions, history, scheduler, organizer, simulator."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import history as history_mod
import scheduler
from analytics import DashboardStats, simulate_cleanup
from exclusions import Exclusions
from file_actions import restore_files
from organizer import SCHEMES, apply_moves, plan_moves
from scanner import FileInfo, human_size


class _Modal(tk.Toplevel):
    def __init__(self, parent, title: str, size: str):
        super().__init__(parent)
        self.title(title)
        self.geometry(size)
        self.transient(parent)
        self.grab_set()


# ---------------------------------------------------------------- exclusions

class ExclusionsDialog(_Modal):
    """Edit folder/extension/keyword exclusion rules; import/export JSON."""

    def __init__(self, parent):
        super().__init__(parent, "Exclusion Rules", "620x420")
        self.rules = Exclusions.load()
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        self.boxes: dict[str, tk.Text] = {}
        specs = [
            ("folders", "Folders (path fragments, one per line)"),
            ("extensions", "Extensions (one per line, e.g. .py)"),
            ("keywords", "Keywords (one per line, e.g. resume)"),
        ]
        for col, (key, label) in enumerate(specs):
            frame = ttk.Frame(body)
            frame.grid(row=0, column=col, sticky="nsew", padx=4)
            body.columnconfigure(col, weight=1)
            body.rowconfigure(0, weight=1)
            ttk.Label(frame, text=label, wraplength=180).pack(anchor="w")
            box = tk.Text(frame, width=24, height=14, undo=True)
            box.pack(fill="both", expand=True)
            box.insert("1.0", "\n".join(getattr(self.rules, key)))
            self.boxes[key] = box

        btns = ttk.Frame(self, padding=(10, 0, 10, 10))
        btns.pack(fill="x")
        ttk.Button(btns, text="Import…", command=self.do_import).pack(side="left")
        ttk.Button(btns, text="Export…", command=self.do_export).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", command=self.save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=4)

    def _collect(self) -> Exclusions:
        vals = {k: [line.strip() for line in box.get("1.0", "end").splitlines()
                    if line.strip()] for k, box in self.boxes.items()}
        return Exclusions(**vals)

    def save(self) -> None:
        if self._collect().save():
            self.destroy()
        else:
            messagebox.showerror("Save failed", "Could not write exclusions.json.",
                                 parent=self)

    def do_export(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json",
            initialfile="exclusions.json",
            filetypes=[("JSON", "*.json")])
        if path and not self._collect().export_to(Path(path)):
            messagebox.showerror("Export failed", "Could not write the file.",
                                 parent=self)

    def do_import(self) -> None:
        path = filedialog.askopenfilename(parent=self,
                                          filetypes=[("JSON", "*.json")])
        if not path:
            return
        imported = Exclusions.import_from(Path(path))
        for key, box in self.boxes.items():
            box.delete("1.0", "end")
            box.insert("1.0", "\n".join(getattr(imported, key)))


# ---------------------------------------------------------------- history

class HistoryWindow(_Modal):
    """Browse cleanup history, export CSV, restore quarantined files."""

    def __init__(self, parent):
        super().__init__(parent, "Cleanup History", "760x440")
        cols = ("time", "mode", "files", "size", "dups", "restored")
        self.tree = ttk.Treeview(self, columns=cols, show="tree headings")
        widths = {"#0": 40, "time": 150, "mode": 90, "files": 60,
                  "size": 90, "dups": 60, "restored": 70}
        for col, title in [("time", "Timestamp"), ("mode", "Mode"),
                           ("files", "Files"), ("size", "Recovered"),
                           ("dups", "Dups"), ("restored", "Restored")]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=widths[col], stretch=(col == "time"))
        self.tree.column("#0", width=widths["#0"], stretch=False)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        self.records: dict[str, dict] = {}
        for rec in reversed(history_mod.load_history()):
            item = self.tree.insert("", "end", text="", values=(
                rec["timestamp"], rec["mode"], rec["count"],
                human_size(rec["total_bytes"]), rec.get("duplicates_removed", 0),
                "yes" if rec.get("restored") else "no"))
            self.records[item] = rec
            for f in rec.get("files", [])[:200]:
                self.tree.insert(item, "end", text="",
                                 values=("", "", "", human_size(f.get("size", 0)),
                                         "", f.get("path", "")))

        btns = ttk.Frame(self, padding=(10, 0, 10, 10))
        btns.pack(fill="x")
        ttk.Button(btns, text="Export CSV…", command=self.export).pack(side="left")
        ttk.Button(btns, text="Restore Selected Cleanup",
                   command=self.restore_selected).pack(side="left", padx=6)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")

    def export(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv",
            initialfile="cleanup_history.csv", filetypes=[("CSV", "*.csv")])
        if path and not history_mod.export_csv(Path(path)):
            messagebox.showerror("Export failed", "Could not write the CSV.",
                                 parent=self)

    def restore_selected(self) -> None:
        sel = self.tree.selection()
        rec = self.records.get(sel[0]) if sel else None
        if not rec:
            messagebox.showinfo("Restore", "Select a cleanup row first.", parent=self)
            return
        if rec.get("mode") != "quarantine":
            messagebox.showinfo(
                "Restore unavailable",
                "Only quarantine cleanups can be restored in-app.\n"
                "Recycle Bin items can be restored from the OS Recycle Bin; "
                "permanent deletions cannot be undone.", parent=self)
            return
        mapping = {f["path"]: f["restore_from"] for f in rec.get("files", [])
                   if f.get("restore_from") and not f.get("restored")}
        result = restore_files(mapping)
        history_mod.mark_restored(rec["timestamp"],
                                  {str(p) for p in result.deleted})
        messagebox.showinfo("Restore", f"Restored {len(result.deleted)} file(s)."
                            + (f" {len(result.failed)} failed." if result.failed else ""),
                            parent=self)


# ---------------------------------------------------------------- scheduler

class SchedulerDialog(_Modal):
    def __init__(self, parent):
        super().__init__(parent, "Scheduled Scans", "420x260")
        sched = scheduler.get_schedule()
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Frequency").grid(row=0, column=0, sticky="w", pady=4)
        self.freq = tk.StringVar(value=sched["frequency"])
        ttk.Combobox(body, textvariable=self.freq, state="readonly",
                     values=["none", "daily", "weekly", "monthly"],
                     width=12).grid(row=0, column=1, sticky="w")

        self.silent = tk.BooleanVar(value=sched["silent"])
        ttk.Checkbutton(body, text="Run silently (no popup unless thresholds hit)",
                        variable=self.silent).grid(row=1, column=0, columnspan=2,
                                                   sticky="w", pady=4)

        ttk.Label(body, text="Notify when reclaimable space exceeds (GB)").grid(
            row=2, column=0, sticky="w", pady=4)
        self.space = tk.DoubleVar(value=sched["notify_space_gb"])
        ttk.Spinbox(body, from_=0.1, to=500, increment=0.5, width=8,
                    textvariable=self.space).grid(row=2, column=1, sticky="w")

        ttk.Label(body, text="Notify when duplicates exceed (files)").grid(
            row=3, column=0, sticky="w", pady=4)
        self.dups = tk.IntVar(value=sched["notify_dup_files"])
        ttk.Spinbox(body, from_=1, to=10000, width=8,
                    textvariable=self.dups).grid(row=3, column=1, sticky="w")

        note = ("Scheduled scans run while the app is open. For unattended "
                "runs, see the README (Task Scheduler / cron).")
        ttk.Label(body, text=note, wraplength=380, style="Muted.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=8)

        btns = ttk.Frame(self, padding=(14, 0, 14, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="Save", command=self.save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=4)

    def save(self) -> None:
        try:
            scheduler.set_schedule(self.freq.get(), self.silent.get(),
                                   float(self.space.get()), int(self.dups.get()))
            self.destroy()
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid values",
                                 "Check the threshold numbers.", parent=self)


# ---------------------------------------------------------------- organizer

class OrganizerDialog(_Modal):
    """Preview and apply automatic file organization."""

    def __init__(self, parent, files: list[FileInfo]):
        super().__init__(parent, "Organize Files", "720x480")
        self.files = files
        self.plans = []

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Sort by").pack(side="left")
        self.scheme = tk.StringVar(value="category")
        ttk.Combobox(top, textvariable=self.scheme, state="readonly",
                     values=list(SCHEMES), width=12).pack(side="left", padx=6)
        ttk.Button(top, text="Destination…", command=self.pick_dest).pack(side="left")
        self.dest_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.dest_var, style="Muted.TLabel").pack(
            side="left", padx=6)
        ttk.Button(top, text="Preview Moves", command=self.preview).pack(side="right")

        cols = ("src", "dst")
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        self.tree.heading("src", text="Current location")
        self.tree.heading("dst", text="Would move to")
        self.tree.column("src", width=330)
        self.tree.column("dst", width=330)
        self.tree.pack(fill="both", expand=True, padx=10, pady=4)

        btns = ttk.Frame(self, padding=(10, 0, 10, 10))
        btns.pack(fill="x")
        self.apply_btn = ttk.Button(btns, text="Apply Moves (0)",
                                    command=self.apply, state="disabled")
        self.apply_btn.pack(side="right")
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right", padx=4)

    def pick_dest(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Organize into…")
        if folder:
            self.dest_var.set(folder)

    def preview(self) -> None:
        if not self.dest_var.get():
            messagebox.showinfo("Destination needed",
                                "Pick a destination folder first.", parent=self)
            return
        self.plans = plan_moves(self.files, Path(self.dest_var.get()),
                                self.scheme.get())
        self.tree.delete(*self.tree.get_children())
        for plan in self.plans[:1000]:
            self.tree.insert("", "end", values=(str(plan.src), str(plan.dst)))
        self.apply_btn.config(text=f"Apply Moves ({len(self.plans)})")
        self.apply_btn.state(["!disabled"] if self.plans else ["disabled"])

    def apply(self) -> None:
        if not self.plans:
            return
        if not messagebox.askyesno("Confirm organization",
                                   f"Move {len(self.plans)} file(s)?",
                                   parent=self):
            return
        moved, failed = apply_moves(self.plans)
        msg = f"Moved {moved} file(s)."
        if failed:
            msg += f" {len(failed)} failed."
        messagebox.showinfo("Organize", msg, parent=self)
        self.destroy()


# ---------------------------------------------------------------- simulator

def show_simulator(parent, selected: list[FileInfo], stats: DashboardStats,
                   action_label: str) -> bool:
    """Cleanup Simulator: preview effects, return True if user confirms."""
    sim = simulate_cleanup(selected, stats)
    names = "\n".join(f"  • {f.name}  ({f.size_human})" for f in selected[:10])
    if len(selected) > 10:
        names += f"\n  … and {len(selected) - 10} more"
    text = (
        f"Simulation — {action_label}\n\n"
        f"Files removed:        {sim['files_removed']}\n"
        f"Storage recovered:    {sim['storage_recovered_h']}\n"
        f"Duplicates removed:   {sim['duplicates_removed']}\n"
        f"Stale files remaining: {sim['remaining_stale']} "
        f"({sim['remaining_stale_bytes_h']})\n\n"
        f"{names}\n\nProceed?"
    )
    return messagebox.askyesno("Cleanup Simulator", text, icon="warning",
                               parent=parent)


class PreviewDialog(_Modal):
    """Generic 'preview before executing' list. Confirms via callback."""

    def __init__(self, parent, title: str, description: str,
                 columns: list, rows: list, on_confirm):
        super().__init__(parent, title, "720x460")
        self._on_confirm = on_confirm
        ttk.Label(self, text=description, wraplength=680,
                  padding=(10, 8)).pack(fill="x")
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns],
                                 show="headings")
        for key, heading, width in columns:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, stretch=True)
        for row in rows[:2000]:
            self.tree.insert("", "end", values=row)
        self.tree.pack(fill="both", expand=True, padx=10)
        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text=f"Confirm ({len(rows)})",
                   command=self._confirm).pack(side="right")
        ttk.Button(btns, text="Cancel",
                   command=self.destroy).pack(side="right", padx=6)

    def _confirm(self) -> None:
        self.destroy()
        self._on_confirm()
