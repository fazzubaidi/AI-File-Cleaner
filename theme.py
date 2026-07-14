"""Light and dark ttk palettes with one-call switching."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

LIGHT = {
    "bg": "#f5f5f7", "surface": "#ffffff", "text": "#1d1d1f",
    "muted": "#6e6e73", "accent": "#0a66c2", "select": "#cfe3f7",
    "danger": "#c0392b", "ok": "#1e8e3e", "warn": "#b26a00",
    "chart": ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
              "#edc948", "#b07aa1", "#9c755f", "#bab0ac", "#86bcb6"],
}
DARK = {
    "bg": "#1e1e22", "surface": "#2a2a30", "text": "#eaeaea",
    "muted": "#9a9aa2", "accent": "#4da3ff", "select": "#31485e",
    "danger": "#ff6b5e", "ok": "#5dd879", "warn": "#ffb454",
    "chart": ["#6ea8dc", "#ffab5e", "#7fc97f", "#ff8a84", "#8fd3ce",
              "#ffe08a", "#cba6d1", "#c8a189", "#d4ccc8", "#a5d6d0"],
}


def apply_theme(root: tk.Tk, dark: bool) -> dict:
    """Restyle every ttk widget class. Returns the active palette."""
    pal = DARK if dark else LIGHT
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=pal["bg"])
    style.configure(".", background=pal["bg"], foreground=pal["text"],
                    fieldbackground=pal["surface"], bordercolor=pal["muted"])
    style.configure("TFrame", background=pal["bg"])
    style.configure("TLabel", background=pal["bg"], foreground=pal["text"])
    style.configure("Muted.TLabel", foreground=pal["muted"])
    style.configure("Card.TFrame", background=pal["surface"])
    style.configure("Card.TLabel", background=pal["surface"],
                    foreground=pal["text"])
    style.configure("CardTitle.TLabel", background=pal["surface"],
                    foreground=pal["muted"], font=("Segoe UI", 9))
    style.configure("CardValue.TLabel", background=pal["surface"],
                    foreground=pal["accent"], font=("Segoe UI", 14, "bold"))
    style.configure("TButton", background=pal["surface"], padding=4)
    style.map("TButton", background=[("active", pal["select"])])
    style.configure("Danger.TButton", foreground=pal["danger"])
    style.configure("TCheckbutton", background=pal["bg"],
                    foreground=pal["text"])
    style.map("TCheckbutton", background=[("active", pal["bg"])])
    style.configure("TEntry", insertcolor=pal["text"])
    style.configure("TNotebook", background=pal["bg"])
    style.configure("TNotebook.Tab", background=pal["bg"],
                    foreground=pal["text"], padding=(12, 5))
    style.map("TNotebook.Tab", background=[("selected", pal["surface"])])
    style.configure("Treeview", background=pal["surface"],
                    foreground=pal["text"], fieldbackground=pal["surface"],
                    rowheight=24)
    style.map("Treeview", background=[("selected", pal["select"])],
              foreground=[("selected", pal["text"])])
    style.configure("Treeview.Heading", background=pal["bg"],
                    foreground=pal["text"], relief="flat")
    style.configure("TProgressbar", background=pal["accent"],
                    troughcolor=pal["surface"])
    style.configure("Status.TLabel", background=pal["surface"],
                    foreground=pal["muted"], relief="sunken", padding=(10, 2))
    return pal
