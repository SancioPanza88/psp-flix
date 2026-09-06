"""PSPflix — Tkinter GUI downloader.

Mirrors the old Streamflix PSP Suite GUI but uses the new
multi-provider `pspflix` package. The provider is chosen by
the user from a dropdown; its language is taken automatically
(each provider serves one language). A default-language filter
("English" by default) is offered for quick browsing.
"""
import io
import os
import re
import sys
import threading

# Windowed PyInstaller builds have no console (sys.stdout/stderr is None), so
# any print() would crash. Redirect to a log file next to the exe before we do
# anything else.
if getattr(sys, "frozen", False) and (sys.stdout is None or sys.stderr is None):
    try:
        _base = os.path.dirname(sys.executable)
        _logf = open(os.path.join(_base, "pspflix.log"), "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _logf
        if sys.stderr is None:
            sys.stderr = _logf
    except Exception:
        class _NullWriter:
            def write(self, *a, **k):
                pass
            def flush(self):
                pass
        if sys.stdout is None:
            sys.stdout = _NullWriter()
        if sys.stderr is None:
            sys.stderr = _NullWriter()

import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk, ImageDraw

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from pspflix import list_providers, get_provider, providers_by_language
from pspflix.providers import PROVIDERS
from pspflix.extractors import extract
from pspflix.models import Movie, TvShow, Episode

from scrapers import SESSION, get_headers, safe_get
from transcoder import transcode_to_psp
from config import load_config, save_config
from version import APP_VERSION, check_for_update


BG_DARK = "#0a0a0a"
BG_CARD = "#181818"
BG_ELEVATED = "#222222"
BG_HOVER = "#2a2a2a"
ACCENT = "#e50914"
ACCENT_HOVER = "#f6121d"
ACCENT_DARK = "#b00710"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#b3b3b3"
TEXT_MUTED = "#737373"
BORDER_COLOR = "#333333"
BORDER_SOFT = "#2a2a2a"
SUCCESS = "#46d369"
WARNING = "#ffb84d"
ERROR = "#e50914"
INFO_BLUE = "#0071eb"


IMAGE_CACHE: dict = {}
_image_semaphore = threading.Semaphore(8)
_USER_AGENT = SESSION.headers.get("User-Agent", "")
_POSTER_SKIP = ("no_image", "placeholder", "spacer", "1x1")


def fetch_poster(url, referer=None):
    if not url or any(kw in url.lower() for kw in _POSTER_SKIP):
        return None
    if url in IMAGE_CACHE:
        return IMAGE_CACHE[url]
    header_sets = []
    image_accept = {"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
    if referer:
        header_sets.append(get_headers(referer=referer, extra=image_accept))
    header_sets.append({"User-Agent": _USER_AGENT, "Accept": "image/*,*/*;q=0.8"})
    if referer:
        header_sets.append(get_headers(referer=referer.rstrip("/") + "/"))
    with _image_semaphore:
        for headers in header_sets:
            try:
                r = safe_get(url, headers=headers, timeout=15)
                if r.status_code == 200 and len(r.content) > 500:
                    IMAGE_CACHE[url] = r.content
                    return r.content
            except Exception:
                continue
    return None


def make_placeholder(width, height, text=""):
    img = Image.new("RGB", (width, height), color=BG_ELEVATED)
    draw = ImageDraw.Draw(img)
    draw.rectangle([2, 2, width - 3, height - 3], outline=BORDER_COLOR)
    if text:
        draw.text((width // 2, height // 2), text, fill=TEXT_SECONDARY, anchor="mm")
    return img


class MovieCard(tk.Frame):
    CARD_W, CARD_H = 140, 210

    def __init__(self, parent, movie, click_callback):
        super().__init__(parent, bg=BG_DARK, bd=0, highlightthickness=0)
        self.movie = movie
        self.click_callback = click_callback
        self.shadow = tk.Frame(self, bg=BG_DARK, bd=0)
        self.shadow.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.inner = tk.Frame(self.shadow, bg=BG_CARD, bd=0,
                              highlightbackground=BORDER_SOFT, highlightthickness=1)
        self.inner.pack(fill=tk.BOTH, expand=True, padx=2, pady=(2, 4))
        self.inner.config(cursor="hand2")
        self.poster_frame = tk.Frame(self.inner, bg=BG_ELEVATED, bd=0)
        self.poster_frame.pack(padx=6, pady=(6, 4))
        self.poster_label = tk.Label(self.poster_frame, bg=BG_ELEVATED, bd=0)
        self.poster_label.pack()

        provider = movie.get("provider", "")
        badge_text = provider if len(provider) <= 16 else provider[:15] + "…"
        self.badge = tk.Label(self.inner, text=f" {badge_text} ", font=("Segoe UI", 7, "bold"),
                              fg=TEXT_PRIMARY, bg=ACCENT, padx=3, pady=1)
        self.badge.place(in_=self.poster_label, anchor="nw", x=4, y=4)

        display_title = movie["title"]
        if len(display_title) > 24:
            display_title = display_title[:22] + "…"
        self.title_label = tk.Label(self.inner, text=display_title, font=("Segoe UI", 9, "bold"),
                                  fg=TEXT_PRIMARY, bg=BG_CARD, wraplength=130, justify=tk.CENTER)
        self.title_label.pack(padx=6, pady=(0, 6))

        is_tv = movie.get("type") == "tv"
        self.type_label = tk.Label(self.inner, text=("  TV SERIES  " if is_tv else "  FILM  "),
                                  font=("Segoe UI", 7, "bold"), fg=BG_DARK,
                                  bg=(INFO_BLUE if is_tv else SUCCESS))
        self.type_label.pack(pady=(0, 8))

        for widget in (self, self.shadow, self.inner, self.poster_frame,
                       self.poster_label, self.title_label, self.type_label, self.badge):
            widget.bind("<Button-1>", lambda _e: self.click_callback(self.movie))
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
        self._show_placeholder()
        threading.Thread(target=self._fetch_image_async, daemon=True).start()

    def _show_placeholder(self, text=""):
        img = make_placeholder(self.CARD_W, self.CARD_H, text)
        photo = ImageTk.PhotoImage(img)
        self.poster_label.config(image=photo, width=self.CARD_W, height=self.CARD_H)
        self._photo = photo

    def _on_enter(self, _event):
        self.inner.config(highlightbackground=ACCENT, highlightthickness=2)
        self.title_label.config(fg=ACCENT_HOVER)

    def _on_leave(self, _event):
        self.inner.config(highlightbackground=BORDER_SOFT, highlightthickness=1)
        self.title_label.config(fg=TEXT_PRIMARY)

    def _fetch_image_async(self):
        url = self.movie.get("poster", "")
        referer = self.movie.get("referer")
        data = fetch_poster(url, referer) if url else None
        if data:
            try:
                img = Image.open(io.BytesIO(data)).convert("RGB")
                img = img.resize((self.CARD_W, self.CARD_H), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.after(0, self._set_image, photo)
                return
            except Exception:
                pass
        self.after(0, self._show_placeholder, "N/D")

    def _set_image(self, photo):
        self.poster_label.config(image=photo, width=self.CARD_W, height=self.CARD_H)
        self._photo = photo


class ScrollableFrame(tk.Frame):
    def __init__(self, parent, bg=BG_DARK):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=bg)
        self.scrollable_frame.bind("<Configure>", self._on_frame_configure)
        self._win_id = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scrollbar_set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self._bind_wheel(self.canvas)
        self._bind_wheel(self.scrollable_frame)

    def _on_scrollbar_set(self, first, last):
        self.scrollbar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scrollbar.pack_forget()
        elif not self.scrollbar.winfo_ismapped():
            self.scrollbar.pack(side="right", fill="y")

    def _on_frame_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self._win_id, width=event.width)

    def _on_mousewheel(self, event):
        if event.delta:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")

    def _bind_wheel(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_mousewheel, add="+")

    def _bind_recursive(self, widget):
        self._bind_wheel(widget)
        for child in widget.winfo_children():
            self._bind_recursive(child)

    def bind_children(self):
        self._bind_recursive(self.scrollable_frame)
        self.refresh_scroll()

    def refresh_scroll(self):
        self.canvas.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))


class EpisodeDialog(tk.Toplevel):
    def __init__(self, parent, movie, on_add_callback):
        super().__init__(parent)
        self.movie = movie
        self.on_add_callback = on_add_callback
        self._seasons_data = []
        self._episode_vars = []
        self.title(f"Episodes — {movie['title']}")
        self.geometry("520x560")
        self.configure(bg=BG_DARK)
        self.transient(parent)
        self.grab_set()
        self._center()
        self._build_ui()
        threading.Thread(target=self._load_seasons, daemon=True).start()

    def _center(self):
        self.update_idletasks()
        x = (self.winfo_screenwidth() - self.winfo_width()) // 2
        y = (self.winfo_screenheight() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _build_ui(self):
        header = tk.Frame(self, bg=BG_DARK)
        header.pack(fill=tk.X, padx=24, pady=(20, 6))
        tk.Label(header, text=self.movie["title"], font=("Segoe UI", 14, "bold"),
                 fg=TEXT_PRIMARY, bg=BG_DARK, wraplength=470).pack(anchor="w")
        tk.Frame(self, bg=ACCENT, height=2).pack(fill=tk.X, padx=24, pady=(0, 12))

        season_row = tk.Frame(self, bg=BG_DARK)
        season_row.pack(fill=tk.X, padx=24, pady=4)
        tk.Label(season_row, text="Season", font=("Segoe UI", 10, "bold"),
                 fg=TEXT_SECONDARY, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 10))
        self._season_var = tk.StringVar()
        self._season_combo = ttk.Combobox(season_row, textvariable=self._season_var,
                                           state="readonly", font=("Segoe UI", 10))
        self._season_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._status_lbl = tk.Label(self, text="Loading seasons...",
                                    font=("Segoe UI", 9, "italic"), fg=TEXT_SECONDARY, bg=BG_DARK)
        self._status_lbl.pack(pady=8)

        self._ep_scroll = ScrollableFrame(self)
        self._ep_scroll.pack(fill=tk.BOTH, expand=True, padx=24, pady=8)
        self._list_frame = self._ep_scroll.scrollable_frame

        btn_row = tk.Frame(self, bg=BG_DARK)
        btn_row.pack(fill=tk.X, padx=24, pady=18)
        ttk.Button(btn_row, text="Select all", command=lambda: self._toggle_all(True)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="Deselect all", command=lambda: self._toggle_all(False)).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="  Add to queue  ", style="Accent.TButton",
                    command=self._add_selected).pack(side=tk.RIGHT)

    def _load_seasons(self):
        try:
            provider = get_provider(self.movie["provider"])
            show = provider.get_tv_show(self.movie["id"])
            seasons = show.seasons or []
            if not seasons:
                self.after(0, lambda: self._status_lbl.config(text="No seasons found.", fg=ERROR))
                return
            self.after(0, self._populate_seasons, seasons)
        except Exception as e:
            self.after(0, lambda: self._status_lbl.config(text=f"Error: {e}", fg=ERROR))

    def _populate_seasons(self, seasons):
        self._seasons_data = seasons
        self._status_lbl.config(text="Select episodes to download.", fg=TEXT_SECONDARY)
        self._season_combo["values"] = [f"Season {s.number}" for s in seasons]
        self._season_combo.current(0)
        self._season_combo.bind("<<ComboboxSelected>>", lambda _e: self._show_episodes())
        self._show_episodes()

    def _show_episodes(self):
        for w in self._list_frame.winfo_children():
            w.destroy()
        idx = self._season_combo.current()
        if idx < 0 or idx >= len(self._seasons_data):
            return
        season = self._seasons_data[idx]
        try:
            episodes = get_provider(self.movie["provider"]).get_episodes_by_season(season.id)
        except Exception:
            episodes = []
        self._episode_vars = []
        for ep in episodes:
            var = tk.BooleanVar(value=True)
            self._episode_vars.append((var, ep))
            row = tk.Frame(self._list_frame, bg=BG_CARD, highlightbackground=BORDER_SOFT, highlightthickness=1)
            row.pack(fill=tk.X, padx=2, pady=3)
            cb = tk.Checkbutton(row, text=f"  Ep. {ep.number:02d}  —  {ep.title or ''}",
                                 variable=var, font=("Segoe UI", 9), fg=TEXT_PRIMARY,
                                 bg=BG_CARD, activeforeground=ACCENT_HOVER, activebackground=BG_CARD,
                                 selectcolor=BG_ELEVATED, anchor="w", bd=0, highlightthickness=0, padx=8, pady=6)
            cb.pack(fill=tk.X)

    def _toggle_all(self, state):
        for var, _ in self._episode_vars:
            var.set(state)

    def _add_selected(self):
        if not self._episode_vars:
            return
        idx = self._season_combo.current()
        if idx < 0 or idx >= len(self._seasons_data):
            return
        season_num = self._seasons_data[idx].number
        added = 0
        for var, ep in self._episode_vars:
            if var.get():
                title = f"{self.movie['title']} - S{season_num:02d}E{ep.number:02d}"
                self.on_add_callback(title, ep.id, self.movie["provider"], self.movie["id"])
                added += 1
        if added > 0:
            messagebox.showinfo("Queue updated", f"Added {added} episodes to the queue.")
            self.destroy()
        else:
            messagebox.showwarning("No episode", "Select at least one episode.")


class StreamflixGUI(tk.Tk):
    _LANG_LABELS = {"it": "🇮🇹 Italiano", "en": "🇬🇧 English", "es": "🇪🇸 Español",
                    "fr": "🇫🇷 Français", "de": "🇩🇪 Deutsch"}
    _LANG_ORDER = ["it", "en", "es", "fr", "de"]

    def __init__(self):
        super().__init__()
        self.title(f"PSPflix  v{APP_VERSION}")
        self.geometry("1100x720")
        self.minsize(900, 600)
        self.configure(bg=BG_DARK)
        _base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        self.downloads_dir = os.path.join(_base, "downloads")
        os.makedirs(self.downloads_dir, exist_ok=True)
        self._cfg = load_config()
        self._setup_styles()
        self.queue_items = []
        self.is_worker_running = False
        self.current_process = None
        langs = {p.language for p in PROVIDERS.values()}
        self._available_langs = [l for l in self._LANG_ORDER if l in langs] + \
            sorted(l for l in langs if l not in self._LANG_ORDER)
        self._build_ui()
        threading.Thread(target=self._check_update_async, daemon=True).start()

    def _check_update_async(self):
        info = check_for_update()
        if info and info.get("available"):
            self.after(0, self._show_update_popup, info)

    def _show_update_popup(self, info):
        latest = info.get("latest", "?")
        url = info.get("url", "")
        if messagebox.askyesno("New version available",
                              f"A new version is available: {latest}\n"
                              f"You are using v{APP_VERSION}.\n\n"
                              "If something does not work, check if you have the latest version first.\n\n"
                              "Open the release page now?"):
            import webbrowser
            webbrowser.open(url)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG_DARK, foreground=TEXT_PRIMARY, font=("Segoe UI", 10))
        style.configure("TLabel", background=BG_DARK, foreground=TEXT_PRIMARY)
        style.configure("TCombobox", fieldbackground=BG_ELEVATED, background=BG_ELEVATED,
                       foreground=TEXT_PRIMARY, arrowcolor=TEXT_PRIMARY, bordercolor=BORDER_COLOR,
                       lightcolor=BG_ELEVATED, darkcolor=BG_ELEVATED, padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", BG_ELEVATED)], bordercolor=[("focus", ACCENT)])
        style.configure("Accent.TButton", background=ACCENT, foreground=TEXT_PRIMARY, borderwidth=0,
                       font=("Segoe UI", 10, "bold"), padding=(18, 9), relief=tk.FLAT)
        style.map("Accent.TButton", background=[("active", ACCENT_HOVER), ("pressed", ACCENT_DARK), ("disabled", "#4a4a4a")])
        style.configure("TButton", background=BG_ELEVATED, foreground=TEXT_PRIMARY, borderwidth=0,
                       font=("Segoe UI", 9, "bold"), padding=(14, 7), relief=tk.FLAT)
        style.map("TButton", background=[("active", BG_HOVER), ("disabled", "#3a3a3a")])
        style.configure("TProgressbar", thickness=7, background=ACCENT, troughcolor=BG_ELEVATED,
                       borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("Vertical.TScrollbar", background=BG_ELEVATED, troughcolor=BG_DARK,
                       bordercolor=BG_DARK, arrowcolor=TEXT_SECONDARY, relief=tk.FLAT, width=10)
        style.map("Vertical.TScrollbar", background=[("active", ACCENT)], arrowcolor=[("active", TEXT_PRIMARY)])

    def _build_ui(self):
        self._build_header()
        self._build_search_bar()
        self._build_content_area()
        self._build_status_bar()

    def _build_header(self):
        row = tk.Frame(self, bg=BG_DARK)
        row.pack(fill=tk.X, padx=28, pady=(22, 2))
        tk.Label(row, text="PSP", font=("Segoe UI", 24, "bold"), fg=ACCENT, bg=BG_DARK).pack(side=tk.LEFT)
        tk.Label(row, text="flix", font=("Segoe UI", 24, "bold"), fg=TEXT_PRIMARY, bg=BG_DARK).pack(side=tk.LEFT)
        tk.Label(row, text="  Download & convert films and TV series for PlayStation Portable",
                 font=("Segoe UI", 10), fg=TEXT_SECONDARY, bg=BG_DARK).pack(side=tk.LEFT, padx=(10, 0), pady=(12, 0))
        accent_strip = tk.Frame(self, bg=ACCENT, height=3)
        accent_strip.pack(fill=tk.X, padx=28, pady=(10, 0))

    def _build_search_bar(self):
        container = tk.Frame(self, bg=BG_ELEVATED, highlightbackground=BORDER_COLOR, highlightthickness=1)
        container.pack(fill=tk.X, padx=28, pady=(16, 18), ipady=4)
        self._search_container = container
        controls = tk.Frame(container, bg=BG_ELEVATED)
        controls.pack(fill=tk.X, padx=14, pady=10)

        # Language selector (filters providers)
        tk.Label(controls, text="Language:", font=("Segoe UI", 10), fg=TEXT_SECONDARY, bg=BG_ELEVATED).pack(side=tk.LEFT, padx=(0, 6))
        self._lang_var = tk.StringVar()
        self._lang_combo = ttk.Combobox(controls, textvariable=self._lang_var, state="readonly", width=14, font=("Segoe UI", 9))
        self._lang_combo["values"] = [self._LANG_LABELS.get(l, l) for l in self._available_langs]
        self._lang_combo.current(0)
        self._lang_combo.pack(side=tk.LEFT, padx=(0, 12))
        self._lang_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_provider_list())

        # Provider selector (filtered by language)
        tk.Label(controls, text="Provider:", font=("Segoe UI", 10), fg=TEXT_SECONDARY, bg=BG_ELEVATED).pack(side=tk.LEFT, padx=(0, 6))
        self._provider_var = tk.StringVar()
        self._provider_combo = ttk.Combobox(controls, textvariable=self._provider_var, state="readonly", width=26, font=("Segoe UI", 9))
        self._provider_combo.pack(side=tk.LEFT, padx=(0, 12))
        self._refresh_provider_list()

        search_frame = tk.Frame(controls, bg=BG_ELEVATED)
        search_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(search_frame, text="🔍", font=("Segoe UI", 12), fg=TEXT_SECONDARY, bg=BG_ELEVATED).pack(side=tk.LEFT, padx=(0, 8))
        self._search_var = tk.StringVar()
        self._search_entry = tk.Entry(search_frame, textvariable=self._search_var, font=("Segoe UI", 12),
                                       bg=BG_ELEVATED, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY, relief=tk.FLAT, bd=0)
        self._search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12), ipady=8, ipadx=4)
        self._search_entry.bind("<Return>", lambda _e: self.start_search())
        self._search_entry.bind("<FocusIn>", lambda _e: container.config(highlightbackground=ACCENT, highlightthickness=2))
        self._search_entry.bind("<FocusOut>", lambda _e: container.config(highlightbackground=BORDER_COLOR, highlightthickness=1))
        self._search_btn = ttk.Button(controls, text="  Search  ", style="Accent.TButton", command=self.start_search)
        self._search_btn.pack(side=tk.LEFT)

    def _selected_language(self):
        idx = self._lang_combo.current()
        if 0 <= idx < len(self._available_langs):
            return self._available_langs[idx]
        return self._available_langs[0]

    def _refresh_provider_list(self):
        lang = self._selected_language()
        names = sorted(n for n, _ in providers_by_language(lang))
        self._provider_combo["values"] = names
        if names:
            self._provider_combo.current(0)

    def _current_provider(self):
        name = self._provider_var.get()
        return name, get_provider(name)

    def _build_content_area(self):
        content = tk.Frame(self, bg=BG_DARK)
        content.pack(fill=tk.BOTH, expand=True, padx=28, pady=(0, 10))
        left = tk.Frame(content, bg=BG_DARK)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        results_header = tk.Frame(left, bg=BG_DARK)
        results_header.pack(fill=tk.X, pady=(0, 10))
        tk.Label(results_header, text="results", font=("Segoe UI", 13, "bold"), fg=TEXT_PRIMARY, bg=BG_DARK).pack(side=tk.LEFT)
        self._results_count_label = tk.Label(results_header, text="", font=("Segoe UI", 9), fg=TEXT_SECONDARY, bg=BG_DARK)
        self._results_count_label.pack(side=tk.LEFT, padx=(8, 0))
        self._results_frame = ScrollableFrame(left)
        self._results_frame.pack(fill=tk.BOTH, expand=True)
        self._placeholder_label = tk.Label(self._results_frame.scrollable_frame,
                                        text="🎬  Search a film or TV series to start", font=("Segoe UI", 12),
                                        fg=TEXT_SECONDARY, bg=BG_DARK)
        self._placeholder_label.pack(expand=True, pady=120)

        queue_pane = tk.Frame(content, bg=BG_CARD, width=300, highlightbackground=BORDER_COLOR, highlightthickness=1)
        queue_pane.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(18, 0))
        queue_pane.pack_propagate(False)
        self._build_queue_panel(queue_pane)

    def _build_queue_panel(self, parent):
        header = tk.Frame(parent, bg=BG_CARD)
        header.pack(fill=tk.X, padx=14, pady=(16, 8))
        tk.Label(header, text="📥 Download queue", font=("Segoe UI", 11, "bold"), fg=TEXT_PRIMARY, bg=BG_CARD).pack(side=tk.LEFT)
        self._queue_count_label = tk.Label(header, text="0", font=("Segoe UI", 9, "bold"), fg=TEXT_PRIMARY, bg=ACCENT, padx=7, pady=1)
        self._queue_count_label.pack(side=tk.RIGHT)
        sep = tk.Frame(parent, bg=BORDER_SOFT, height=1)
        sep.pack(fill=tk.X, padx=14, pady=(0, 8))
        self._queue_scroll = ScrollableFrame(parent, bg=BG_CARD)
        self._queue_scroll.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self._show_empty_queue_placeholder()

    def _build_status_bar(self):
        bar = tk.Frame(self, bg=BG_ELEVATED, height=34)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self._status_label = tk.Label(bar, text="●  Ready", font=("Segoe UI", 9, "bold"), fg=SUCCESS, bg=BG_ELEVATED)
        self._status_label.pack(side=tk.LEFT, padx=16, pady=6)
        tk.Label(bar, text=f"📁  {self.downloads_dir}", font=("Segoe UI", 8), fg=TEXT_MUTED, bg=BG_ELEVATED).pack(side=tk.RIGHT, padx=16)

    def set_status(self, text):
        if "Error" in text:
            color, dot = ERROR, "●"
        elif "Ready" in text:
            color, dot = SUCCESS, "●"
        elif "ing" in text or "ading" in text:
            color, dot = ACCENT, "●"
        else:
            color, dot = TEXT_SECONDARY, "●"
        self._status_label.config(text=f"{dot}  {text}", fg=color)

    def start_search(self):
        query = self._search_var.get().strip()
        if not query:
            messagebox.showwarning("Empty search", "Enter a title to search.")
            return
        for w in self._results_frame.scrollable_frame.winfo_children():
            w.destroy()
        tk.Label(self._results_frame.scrollable_frame, text=f'Searching "{query}"...',
                 font=("Segoe UI", 12), fg=ACCENT, bg=BG_DARK).pack(expand=True, pady=120)
        self.set_status("Searching...")
        self._search_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()

    def _search_thread(self, query):
        try:
            _, provider = self._current_provider()
            results = provider.search(query)
            results = [r for r in results if isinstance(r, (Movie, TvShow))]
            self.after(0, self._display_results, results)
        except Exception as e:
            self.after(0, self._on_search_error, str(e))

    def _on_search_error(self, err_msg):
        self._search_btn.config(state=tk.NORMAL)
        self.set_status("Search error.")
        messagebox.showerror("Search error", f"An error occurred:\n{err_msg}")

    def _display_results(self, results):
        self._search_btn.config(state=tk.NORMAL)
        for w in self._results_frame.scrollable_frame.winfo_children():
            w.destroy()
        if not results:
            self.set_status("No results found.")
            self._results_count_label.config(text="")
            tk.Label(self._results_frame.scrollable_frame, text="No results.\nTry other keywords.",
                     font=("Segoe UI", 12), fg=TEXT_SECONDARY, bg=BG_DARK, justify=tk.CENTER).pack(expand=True, pady=120)
            return
        self.set_status(f"Found {len(results)} items.")
        self._results_count_label.config(text=f"({len(results)})")
        provider_name, _ = self._current_provider()
        cards = []
        for r in results:
            item = {
                "title": r.title,
                "id": r.id,
                "type": "tv" if isinstance(r, TvShow) else "movie",
                "poster": getattr(r, "poster", None),
                "referer": provider_name,
                "provider": provider_name,
            }
            cards.append(item)
        cols = 4
        for c in range(cols):
            self._results_frame.scrollable_frame.columnconfigure(c, weight=1, uniform="col")
        for idx, item in enumerate(cards):
            card = MovieCard(self._results_frame.scrollable_frame, item, self._on_card_click)
            card.grid(row=idx // cols, column=idx % cols, padx=6, pady=8, sticky="n")
        self._results_frame.bind_children()

    def _on_card_click(self, movie):
        if movie.get("type") == "tv":
            EpisodeDialog(self, movie, self.add_to_queue)
        else:
            if messagebox.askyesno("Confirm download", f"Add to queue?\n\n{movie['title']}"):
                self.add_to_queue(movie["title"], movie["id"], movie["provider"], movie["id"])

    def _show_empty_queue_placeholder(self):
        for w in self._queue_scroll.scrollable_frame.winfo_children():
            if getattr(w, "_is_empty_placeholder", False):
                return
        lbl = tk.Label(self._queue_scroll.scrollable_frame, text="No downloads in queue",
                       font=("Segoe UI", 9), fg=TEXT_SECONDARY, bg=BG_CARD)
        lbl._is_empty_placeholder = True
        lbl.pack(pady=30)

    def add_to_queue(self, title, vid, provider_name, item_id):
        item = {
            "title": title, "vid": vid, "provider": provider_name, "item_id": item_id,
            "status": "Queued", "progress": 0.0, "widgets": {},
        }
        self.queue_items.append(item)
        self._draw_queue_card(item)
        self._queue_count_label.config(text=str(len(self.queue_items)))
        if not self.is_worker_running:
            self.is_worker_running = True
            threading.Thread(target=self._queue_worker, daemon=True).start()

    def _draw_queue_card(self, item):
        for w in self._queue_scroll.scrollable_frame.winfo_children():
            if getattr(w, "_is_empty_placeholder", False):
                w.destroy()
        card = tk.Frame(self._queue_scroll.scrollable_frame, bg=BG_ELEVATED,
                         highlightbackground=BORDER_SOFT, highlightthickness=1)
        card.pack(fill=tk.X, padx=4, pady=5)
        top = tk.Frame(card, bg=BG_ELEVATED)
        top.pack(fill=tk.X, padx=10, pady=(8, 2))
        short = item["title"] if len(item["title"]) <= 28 else item["title"][:25] + "…"
        tk.Label(top, text=short, font=("Segoe UI", 9, "bold"), fg=TEXT_PRIMARY, bg=BG_ELEVATED, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
        cancel_btn = tk.Button(top, text="✕", font=("Segoe UI", 9, "bold"), fg=TEXT_SECONDARY,
                                bg=BG_ELEVATED, activeforeground=TEXT_PRIMARY, activebackground=ERROR,
                                bd=0, width=2, relief=tk.FLAT, cursor="hand2", command=lambda: self._cancel_item(item))
        cancel_btn.pack(side=tk.RIGHT)
        status_lbl = tk.Label(card, text="⏳ Queued", font=("Segoe UI", 8), fg=TEXT_SECONDARY, bg=BG_ELEVATED, anchor="w")
        status_lbl.pack(fill=tk.X, padx=10, pady=(0, 3))
        pbar = ttk.Progressbar(card, mode="determinate")
        pbar.pack(fill=tk.X, padx=10, pady=(0, 10))
        item["widgets"] = {"card": card, "status_lbl": status_lbl, "pbar": pbar, "cancel_btn": cancel_btn}
        self._queue_scroll.bind_children()

    def _cancel_item(self, item):
        status = item["status"]
        if status in ("Queued",):
            if item in self.queue_items:
                self.queue_items.remove(item)
            item["widgets"]["card"].destroy()
            self._refresh_queue_count()
        elif "Download" in status or "Resolving" in status or "Segments" in status or "Converting" in status:
            if messagebox.askyesno("Cancel download", "Cancel this download?"):
                item["status"] = "Cancelled"
                if self.current_process:
                    try:
                        self.current_process.kill()
                    except Exception:
                        pass
                self._update_item(item, "Cancelled", 0.0)
                item["widgets"]["cancel_btn"].config(state=tk.DISABLED, bg=BG_ELEVATED)
        else:
            if item in self.queue_items:
                self.queue_items.remove(item)
            item["widgets"]["card"].destroy()
            self._refresh_queue_count()

    def _refresh_queue_count(self):
        self._queue_count_label.config(text=str(len(self.queue_items)))
        if not self.queue_items:
            self._show_empty_queue_placeholder()

    def _update_item(self, item, status, progress):
        item["status"] = status
        item["progress"] = progress
        widgets = item.get("widgets", {})
        try:
            if "status_lbl" in widgets:
                color = TEXT_SECONDARY
                icon = "⏳"
                if "Download" in status or "Resolving" in status or "Segments" in status or "Converting" in status:
                    color = ACCENT; icon = "⬇"
                elif "Complete" in status:
                    color = SUCCESS; icon = "✅"
                elif "Cancelled" in status:
                    color = WARNING; icon = "⛔"
                elif "Error" in status:
                    color = ERROR; icon = "❌"
                widgets["status_lbl"].config(text=f"{icon} {status}", fg=color)
            if "pbar" in widgets:
                widgets["pbar"]["value"] = progress
        except tk.TclError:
            return
        if "Download" in status or "%" in status:
            self.set_status(f"Processing: {item['title']} ({progress:.1f}%)")

    def _queue_worker(self):
        while True:
            pending = next((i for i in self.queue_items if i["status"] == "Queued"), None)
            if not pending:
                break
            self._process_item(pending)
        self.is_worker_running = False
        self.after(0, lambda: self.set_status("Ready"))

    def _resolve_server(self, provider, vid, item_id, referer):
        """Return (stream_url, video_headers_dict) or (None, {})."""
        vtype = "episode" if item_id != vid else "movie"
        try:
            servers = provider.get_servers(vid, vtype)
        except Exception as e:
            print(f"[GUI] get_servers error: {e}")
            return None, {}
        if not servers:
            return None, {}
        for srv in servers:
            try:
                video = provider.get_video(srv)
                if video and video.source:
                    return video.source, dict(getattr(video, "headers", {}) or {})
            except Exception as e:
                print(f"[GUI] server {getattr(srv,'name','?')} failed: {e}")
        return None, {}

    def _process_item(self, item):
        output_path = ""
        try:
            provider = get_provider(item["provider"])
            self.after(0, lambda: self._update_item(item, "Resolving link...", 0.0))
            stream_url, video_headers = self._resolve_server(provider, item["vid"], item["item_id"], provider.base_url)
            if item["status"] == "Cancelled":
                return
            if not stream_url:
                self.after(0, lambda: self._update_item(item, "Link error", 0.0))
                return
            safe_title = re.sub(r'[\\/*?:"<>|]', "", item["title"]).strip()
            output_path = os.path.join(self.downloads_dir, f"{safe_title}.mp4")
            counter = 1
            while os.path.exists(output_path):
                output_path = os.path.join(self.downloads_dir, f"{safe_title}_{counter}.mp4")
                counter += 1
            self.after(0, lambda: self._update_item(item, "Download 0.0%", 0.0))
            headers = self._build_ffmpeg_headers(video_headers)
            success = transcode_to_psp(
                stream_url, output_path, headers=headers,
                progress_callback=lambda pct: self._on_progress(item, pct),
                cancel_check=lambda: item["status"] == "Cancelled",
                refresh_url_callback=lambda: self._resolve_server(provider, item["vid"], item["item_id"], provider.base_url)[0],
            )
            if item["status"] == "Cancelled":
                if os.path.exists(output_path):
                    os.remove(output_path)
                self.after(0, lambda: self._update_item(item, "Cancelled", 0.0))
            elif success:
                self.after(0, lambda: self._update_item(item, "Complete", 100.0))
            else:
                self.after(0, lambda: self._update_item(item, "FFmpeg error", 0.0))
        except Exception as e:
            if item["status"] == "Cancelled":
                if output_path and os.path.exists(output_path):
                    os.remove(output_path)
                self.after(0, lambda: self._update_item(item, "Cancelled", 0.0))
            else:
                err = str(e)[:20]
                self.after(0, lambda: self._update_item(item, f"Error: {err}", 0.0))

    def _on_progress(self, item, pct):
        if item["status"] != "Cancelled":
            label = "Segments" if pct < 90.0 else "Converting"
            self.after(0, lambda p=pct, lbl=label: self._update_item(item, f"{lbl} {p:.1f}%", p))

    @staticmethod
    def _build_ffmpeg_headers(video_headers=None):
        """Build the ffmpeg header string straight from the resolved Video's
        headers. The extractor already sets the exact Referer/User-Agent/Cookie
        that StreamflixReborn passes to the player, so we pass them through
        verbatim and only add a User-Agent fallback if missing."""
        hdrs = dict(video_headers or {})
        if not any(k.lower() == "user-agent" for k in hdrs):
            hdrs["User-Agent"] = SESSION.headers.get("User-Agent", "")
        lines = [f"{key}: {value}" for key, value in hdrs.items() if value]
        return "\r\n".join(lines) + "\r\n"


if __name__ == "__main__":
    app = StreamflixGUI()
    app.mainloop()
