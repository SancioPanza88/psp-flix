"""
PSP Thumbnail Maker — a simple standalone app.

What it does:
  1. Pick a video (mp4).
  2. The app automatically extracts a frame from the middle of the video to
     use as the cover, or you can choose your own custom image.
  3. Press "Create thumbnail" and the app saves a .THM file (same name as the
     video, with the .THM extension) in the same folder as the video.

The PSP, in the Video → Memory Stick menu, only shows the preview when a file
with the same name and the ".THM" extension (a small 160x120 JPEG) exists in
the same folder as the .mp4 file. Without this file it shows the generic white
icon.

Requires:
  - Python 3 with Pillow installed (pip install pillow)
  - ffmpeg available on the PATH (only needed to extract the frame from the
    video automatically; if you choose your own image, ffmpeg is not needed)

Run:
  python psp_thumbnail_maker.py
"""

import os
import sys
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageTk

# A windowed (console=False) PyInstaller exe has no console; child processes
# must not open one and must have all std streams redirected, or ffmpeg/ffprobe
# can hang forever.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

# ---------------------------------------------------------------------------
# FFmpeg path resolver
# ---------------------------------------------------------------------------

def _get_ffmpeg():
    """Look for ffmpeg.exe next to the exe, otherwise use the system PATH."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(base, "ffmpeg.exe")
    return local if os.path.exists(local) else "ffmpeg"


def _get_ffprobe():
    """Look for ffprobe.exe next to the exe, otherwise use the system PATH."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(base, "ffprobe.exe")
    return local if os.path.exists(local) else "ffprobe"

# ---------------------------------------------------------------------------
# Theme (simple, consistent with Streamflix PSP Suite)
# ---------------------------------------------------------------------------

BG = "#121212"
PANEL = "#1c1c1c"
ACCENT = "#e50914"
TEXT = "#ffffff"
TEXT_DIM = "#a0a0a0"

THM_SIZE = (160, 120)


def get_video_duration(video_path):
    """Return the video duration in seconds using ffprobe, or None."""
    try:
        result = subprocess.run(
            [_get_ffprobe(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, text=True,
            creationflags=_NO_WINDOW,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def extract_frame(video_path, timestamp_seconds, out_path):
    """Extract a frame from the video with ffmpeg and save it as JPEG."""
    cmd = [
        _get_ffmpeg(), "-y", "-nostdin",
        "-ss", str(max(0, timestamp_seconds)),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        out_path,
    ]
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=30, creationflags=_NO_WINDOW)
    return result.returncode == 0 and os.path.exists(out_path)


class ThumbnailMakerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PSP Thumbnail Maker")
        self.geometry("420x560")
        self.resizable(False, False)
        self.configure(bg=BG)

        self.video_path = None
        self.image_path = None   # source image for the cover (custom or extracted)
        self._preview_photo = None
        self._video_duration = None
        self._extract_step = 0  # advances on every click of "Extract from video"

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        tk.Label(self, text="PSP Thumbnail Maker", font=("Segoe UI", 16, "bold"),
                 fg=ACCENT, bg=BG).pack(pady=(20, 4))
        tk.Label(self, text="Create the .THM cover for videos on the PSP",
                 font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG).pack(pady=(0, 16))

        # --- Video selection ---
        tk.Button(self, text="1. Choose the video (.mp4)", font=("Segoe UI", 10),
                   command=self.choose_video, bg=PANEL, fg=TEXT, relief=tk.FLAT,
                   activebackground=ACCENT, cursor="hand2").pack(fill=tk.X, padx=24, pady=4, ipady=8)

        self.video_label = tk.Label(self, text="No video selected", font=("Segoe UI", 8),
                                     fg=TEXT_DIM, bg=BG, wraplength=380)
        self.video_label.pack(pady=(0, 12))

        # --- Preview ---
        self.preview_frame = tk.Label(self, bg=PANEL, width=THM_SIZE[0], height=THM_SIZE[1])
        self.preview_frame.pack(pady=4)
        self._show_blank_preview()

        # --- Cover source choice ---
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=12)
        tk.Button(btn_row, text="Extract from video", font=("Segoe UI", 9),
                   command=self.extract_from_video, bg=PANEL, fg=TEXT, relief=tk.FLAT,
                   activebackground=ACCENT, cursor="hand2").pack(side=tk.LEFT, padx=4, ipadx=6, ipady=4)
        tk.Button(btn_row, text="Choose image...", font=("Segoe UI", 9),
                   command=self.choose_custom_image, bg=PANEL, fg=TEXT, relief=tk.FLAT,
                   activebackground=ACCENT, cursor="hand2").pack(side=tk.LEFT, padx=4, ipadx=6, ipady=4)

        # --- Status ---
        self.status_label = tk.Label(self, text="", font=("Segoe UI", 9), fg=TEXT_DIM, bg=BG, wraplength=380)
        self.status_label.pack(pady=(8, 4))

        # --- Create thumbnail ---
        self.create_btn = tk.Button(self, text="Create thumbnail (.THM)", font=("Segoe UI", 11, "bold"),
                                     command=self.create_thumbnail, bg=ACCENT, fg=TEXT, relief=tk.FLAT,
                                     activebackground="#f40612", cursor="hand2", state=tk.DISABLED)
        self.create_btn.pack(fill=tk.X, padx=24, pady=(16, 20), ipady=10)

    # ------------------------------------------------------------------
    def _show_blank_preview(self):
        img = Image.new("RGB", THM_SIZE, color=PANEL)
        photo = ImageTk.PhotoImage(img)
        self.preview_frame.config(image=photo)
        self._preview_photo = photo

    def _set_preview(self, image_path):
        try:
            img = Image.open(image_path).convert("RGB")
            img = img.resize(THM_SIZE, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.preview_frame.config(image=photo)
            self._preview_photo = photo
        except Exception:
            self._show_blank_preview()

    def _set_status(self, text, color=None):
        self.status_label.config(text=text, fg=color or TEXT_DIM)

    def _refresh_create_button(self):
        self.create_btn.config(state=tk.NORMAL if (self.video_path and self.image_path) else tk.DISABLED)

    # ------------------------------------------------------------------
    def choose_video(self):
        path = filedialog.askopenfilename(
            title="Choose the video",
            filetypes=[("MP4 Video", "*.mp4"), ("All files", "*.*")],
        )
        if not path:
            return
        self.video_path = path
        self.video_label.config(text=os.path.basename(path))
        self.image_path = None
        self._video_duration = None
        self._extract_step = 0
        self._show_blank_preview()
        self._set_status("Video selected. Now extract a frame or choose an image.")
        self._refresh_create_button()
        # Auto-extract right away, for convenience
        self.extract_from_video()

    def extract_from_video(self):
        if not self.video_path:
            messagebox.showwarning("No video", "Choose a video first.")
            return
        self._set_status("Extracting frame...", ACCENT)
        threading.Thread(target=self._extract_from_video_thread, daemon=True).start()

    # Video points (as a percentage of the duration) to cycle through on each
    # click of "Extract from video", so every click shows a different shot
    # instead of always staying on the same one.
    _EXTRACT_POINTS = (0.5, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90, 0.20, 0.65, 0.10)

    def _extract_from_video_thread(self):
        if self._video_duration is None:
            self._video_duration = get_video_duration(self.video_path)

        duration = self._video_duration
        if duration:
            frac = self._EXTRACT_POINTS[self._extract_step % len(self._EXTRACT_POINTS)]
            timestamp = duration * frac
        else:
            # Duration unavailable: advance by a few seconds on each click
            # anyway, so at least it doesn't always stay on the same frame.
            timestamp = 5 + (self._extract_step * 7)
        self._extract_step += 1

        tmp_path = os.path.join(tempfile.gettempdir(), "psp_thumb_preview.jpg")
        ok = extract_frame(self.video_path, timestamp, tmp_path)
        if ok:
            self.image_path = tmp_path
            self.after(0, self._set_preview, tmp_path)
            self.after(0, self._set_status, "Frame extracted from video. Click again for a different shot.", None)
        else:
            self.after(0, self._set_status,
                       "Could not extract the frame (ffmpeg not found?). "
                       "You can choose a custom image instead.", "#ff6666")
        self.after(0, self._refresh_create_button)

    def choose_custom_image(self):
        path = filedialog.askopenfilename(
            title="Choose an image for the cover",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"), ("All files", "*.*")],
        )
        if not path:
            return
        self.image_path = path
        self._set_preview(path)
        self._set_status("Custom image selected.")
        self._refresh_create_button()

    # ------------------------------------------------------------------
    def create_thumbnail(self):
        if not (self.video_path and self.image_path):
            return
        try:
            base, _ext = os.path.splitext(self.video_path)
            thm_path = base + ".THM"

            img = Image.open(self.image_path).convert("RGB")
            img = img.resize(THM_SIZE, Image.Resampling.LANCZOS)
            img.save(thm_path, "JPEG", quality=90)

            self._set_status(f"Thumbnail created: {os.path.basename(thm_path)}", "#46d369")
            messagebox.showinfo(
                "Done",
                f"Thumbnail created successfully:\n{thm_path}\n\n"
                "Copy the .mp4 file and the .THM file together into the "
                "Memory Stick/VIDEO/ folder on the PSP.",
            )
        except Exception as e:
            self._set_status(f"Error: {e}", "#ff6666")
            messagebox.showerror("Error", f"Could not create the thumbnail:\n{e}")


if __name__ == "__main__":
    app = ThumbnailMakerApp()
    app.mainloop()
