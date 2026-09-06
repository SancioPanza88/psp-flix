"""Cloudflare browser session helper.

Some hosts (vixcloud.co) protect their embed/playlist endpoints with
Cloudflare's JavaScript challenge and bind the playback token to a real
browser session. Plain HTTP requests get 403 / "Just a moment...".

This module lazily launches a single headless undetected-chromedriver
instance, navigates to a URL, waits for Cloudflare to clear, and returns
the rendered HTML plus the session cookies. The browser is reused across
calls and shut down at interpreter exit.
"""
import os
import time
import atexit
import threading

_lock = threading.Lock()
_driver = None
_driver_failed = False

_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

_CHALLENGE_MARKERS = ("Just a moment", "Checking your browser",
                      "cf-browser-verification", "challenge-platform")


def _find_chrome():
    for p in _CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def _detect_chrome_major():
    """Best-effort detection of the installed Chrome major version."""
    exe = _find_chrome()
    if not exe:
        return None
    try:
        import subprocess
        # Chrome on Windows doesn't reliably print --version, read from folder.
        version_dir = os.path.dirname(exe)
        for entry in os.listdir(version_dir):
            if entry and entry[0].isdigit() and "." in entry:
                return int(entry.split(".")[0])
    except Exception:
        pass
    return None


def _build_driver():
    import undetected_chromedriver as uc

    opts = uc.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,720")

    kwargs = {"options": opts}
    exe = _find_chrome()
    if exe:
        kwargs["browser_executable_path"] = exe
    major = _detect_chrome_major()
    if major:
        kwargs["version_main"] = major
    return uc.Chrome(**kwargs)


def _get_driver():
    global _driver, _driver_failed
    if _driver_failed:
        return None
    if _driver is not None:
        return _driver
    try:
        _driver = _build_driver()
        atexit.register(_shutdown)
    except Exception as e:
        print(f"[CF] could not start browser: {e}")
        _driver_failed = True
        _driver = None
    return _driver


def _shutdown():
    global _driver
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


def is_available():
    return _find_chrome() is not None and not _driver_failed


def fetch(url, wait=9.0, settle_markers=("window.video", "window.masterPlaylist"),
          max_wait=25.0):
    """Load `url` in the CF browser and return (html, cookies_dict) or (None, {}).

    Waits until the challenge clears and one of `settle_markers` appears
    (or `max_wait` elapses).
    """
    with _lock:
        driver = _get_driver()
        if driver is None:
            return None, {}
        try:
            driver.get(url)
        except Exception as e:
            print(f"[CF] navigation error: {e}")
            return None, {}

        deadline = time.time() + max_wait
        html = ""
        time.sleep(min(wait, max_wait))
        while time.time() < deadline:
            try:
                html = driver.page_source or ""
            except Exception:
                html = ""
            challenged = any(m in html for m in _CHALLENGE_MARKERS)
            settled = (not settle_markers) or any(m in html for m in settle_markers)
            if html and not challenged and settled:
                break
            time.sleep(1.5)

        cookies = {}
        try:
            for c in driver.get_cookies():
                cookies[c["name"]] = c["value"]
        except Exception:
            pass
        return html, cookies
