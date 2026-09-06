import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import requests
from urllib.parse import urljoin

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


# In a windowed (console=False) PyInstaller build, sys.stdout / sys.stderr are
# None. Every print() would then raise AttributeError and abort the transcode
# mid-way (which looked like "ffmpeg froze"). Redirect them to a log file next
# to the exe so (a) prints never crash and (b) we get a real log to debug from.
if getattr(sys, 'frozen', False) and (sys.stdout is None or sys.stderr is None):
    try:
        _logf = open(os.path.join(APP_DIR, "pspflix.log"), "a", encoding="utf-8", buffering=1)
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


# On Windows, a windowed (console=False) PyInstaller exe has no console. Any
# child process we spawn must NOT try to open one, and we must fully redirect
# its std streams, otherwise ffmpeg can deadlock/hang forever waiting on a
# handle that doesn't exist. These flags/options are applied to every
# subprocess call below.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _get_ffmpeg():
    """Restituisce il percorso di ffmpeg.
    Se siamo dentro un exe PyInstaller (o comunque accanto all'exe),
    cerca ffmpeg.exe nella stessa cartella dell'eseguibile.
    Altrimenti usa 'ffmpeg' dal PATH di sistema."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = APP_DIR
    local = os.path.join(base, "ffmpeg.exe")
    return local if os.path.exists(local) else "ffmpeg"


def _cleanup_stale_temp_dirs():
    """Rimuove eventuali cartelle _temp_hls_* rimaste da chiusure anomale
    di sessioni precedenti (es. crash, kill del processo). La cartella
    temp della sessione corrente viene sempre rimossa a fine job in
    transcode_to_psp (blocco finally), quindi questa funzione si occupa
    solo della "spazzatura" lasciata indietro da run passate."""
    try:
        for name in os.listdir(APP_DIR):
            if name.startswith("_temp_hls_"):
                full = os.path.join(APP_DIR, name)
                if os.path.isdir(full):
                    shutil.rmtree(full, ignore_errors=True)
    except Exception:
        pass


_cleanup_stale_temp_dirs()

# ---------------------------------------------------------------------------
# DNS-over-HTTPS resolution (mirrors StreamFlix's DnsResolver.kt)
#
# Some ISPs block/poison DNS resolution for the streaming CDN hosts used by
# these providers, which shows up as a plain TCP connect timeout (the
# request never even reaches the server) — not an HTTP error, not a token
# issue, and it's consistent on the exact same host no matter how many
# times you retry or how many fresh links you request. StreamFlix works
# around this by resolving hostnames via DNS-over-HTTPS (Cloudflare)
# instead of the system/ISP resolver. We do the same thing here, globally,
# by patching urllib3's connection-establishment function so every
# requests.Session in the app benefits, without touching TLS/SNI behavior
# (certificate validation still happens against the original hostname).
# ---------------------------------------------------------------------------

import socket
from urllib3.util import connection as _urllib3_connection

_DOH_ENDPOINT = "https://1.1.1.1/dns-query"  # IP literal: no DNS needed to reach it
_doh_cache = {}
_doh_cache_lock = threading.Lock()
_doh_failures = 0  # consecutive failures -> disable DoH (circuit-breaker)
_doh_disabled = False


def _doh_resolve(hostname):
    """Resolve a hostname via Cloudflare DNS-over-HTTPS. Returns a list of
    IP strings, or None if resolution fails (caller should fall back to
    the system resolver)."""
    global _doh_failures, _doh_disabled
    if _doh_disabled:
        return None
    with _doh_cache_lock:
        cached = _doh_cache.get(hostname)
    if cached:
        return cached

    try:
        r = requests.get(
            _DOH_ENDPOINT,
            params={"name": hostname, "type": "A"},
            headers={"accept": "application/dns-json"},
            timeout=4,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        ips = [a["data"] for a in data.get("Answer", []) if a.get("type") == 1]
        with _doh_cache_lock:
            _doh_failures = 0
            if ips:
                _doh_cache[hostname] = ips
            return ips or None
    except Exception as e:
        print(f"[DoH] Resolution failed for {hostname}: {e}")
        with _doh_cache_lock:
            _doh_failures += 1
            if _doh_failures >= 3 and not _doh_disabled:
                print("[DoH] Endpoint irraggiungibile, passo al DNS di sistema.")
                _doh_disabled = True
    return None


_orig_create_connection = _urllib3_connection.create_connection


def _doh_create_connection(address, *args, **kwargs):
    """Drop-in replacement for urllib3's create_connection that resolves
    the hostname via DoH first, then falls back to a forced IPv4 system
    lookup, then finally to whatever the default resolver does.

    Forcing IPv4 in the fallback matters: an intermittent "works / doesn't
    work" pattern on the exact same host (rather than a consistent failure)
    is the classic signature of a broken/unstable IPv6 path — the resolver
    alternates between handing back a working IPv4 address and a
    dead/filtered IPv6 one. Skipping IPv6 entirely avoids that coin flip.
    """
    host, port = address
    is_literal_ip = host and host.replace(".", "").isdigit()

    if host and not is_literal_ip and host != "localhost":
        candidates = []

        doh_ips = _doh_resolve(host)
        if doh_ips:
            candidates.extend(doh_ips)

        # Forced IPv4-only system resolution as a second source of
        # candidate addresses (skips any broken IPv6 records).
        try:
            for family, _, _, _, sockaddr in socket.getaddrinfo(host, port, family=socket.AF_INET):
                ip = sockaddr[0]
                if ip not in candidates:
                    candidates.append(ip)
        except Exception:
            pass

        last_exc = None
        for ip in candidates:
            for _ in range(2):  # a couple of quick tries per candidate IP
                try:
                    return _orig_create_connection((ip, port), *args, **kwargs)
                except Exception as e:
                    last_exc = e
        if candidates:
            print(f"[DoH] All {len(candidates)} candidate IP(s) for {host} failed, "
                  f"falling back to default resolver: {last_exc}")

    return _orig_create_connection(address, *args, **kwargs)


_urllib3_connection.create_connection = _doh_create_connection

# ---------------------------------------------------------------------------
# PSP encoding parameters (shared between playlist-build and ffmpeg command)
# ---------------------------------------------------------------------------

# PSP native screen is 480x272. Exact filter from the original working build.
PSP_VIDEO_FILTER = (
    "scale=480:272:force_original_aspect_ratio=decrease,"
    "pad=480:272:(ow-iw)/2:(oh-ih)/2"
)

# Number of HLS segments downloaded in parallel. Over a VPN the request
# latency (not bandwidth) is the bottleneck, so more concurrent requests keeps
# the pipe full. The session's connection pool is sized to match this.
SEGMENT_WORKERS = 16

def _build_encode_args():
    """ffmpeg encode arguments for PSP-compatible output.

    La PSP accetta SOLO MP4 con brand MSNV + atom uuid PROF (muxer 'psp'
    di ffmpeg). Con '-f mp4 -brand mp42' si ottiene un MP4 generico che
    sul PC si apre ma sulla PSP dà 'Dati danneggiati' / 'Formato non
    supportato'. Per questo si usa '-f psp' (scrive da solo MSNV + uuid)
    e NON si passa alcun '-brand' che lo sovrascriverebbe.
    Audio a 48000 Hz = standard PSP (44100 a volte rifiutato).
    """
    return [
        "-vf", PSP_VIDEO_FILTER,
        "-r", "29.97",
        "-fps_mode", "cfr",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-threads", "0",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-pix_fmt", "yuv420p",
        "-bf", "0",
        "-refs", "2",
        "-g", "15",
        "-keyint_min", "15",
        "-sc_threshold", "0",
        "-b:v", "768k",
        "-maxrate", "768k",
        "-bufsize", "1536k",
        "-c:a", "aac",
        "-profile:a", "aac_low",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        "-f", "psp",
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_time(time_str):
    """Convert HH:MM:SS.xx to seconds."""
    parts = time_str.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return float(h) * 3600 + float(m) * 60 + float(s)
    return 0.0


def parse_headers_str(headers_str):
    """Parse an ffmpeg-style header string into a dict."""
    result = {}
    if not headers_str:
        return result
    for line in headers_str.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split(":", 1)
        if len(parts) == 2:
            result[parts[0].strip()] = parts[1].strip()
    return result


def _default_progress(percent):
    bar_len = 30
    filled = int(bar_len * percent // 100)
    bar = "=" * filled + ">" + " " * (bar_len - filled - 1)
    sys.stdout.write(f"\r[Transcoder] [{bar}] {percent:.1f}%")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def transcode_to_psp(source_url, output_path, headers=None, progress_callback=None, cancel_check=None,
                      refresh_url_callback=None):
    """
    Download an HLS stream and transcode it to a PSP-compatible MP4.

    Steps:
      1. Fetch the master/variant playlist.
      2. Download all segments (and encryption keys) in parallel.
      3. Run ffmpeg on the local playlist to produce the final MP4.

    Args:
        source_url:           HLS playlist URL (master or media).
        output_path:          Destination MP4 path.
        headers:              Optional ffmpeg-style header string passed to requests.
        progress_callback:    Callable(float) — receives 0–100 progress.
        cancel_check:         Callable() -> bool — returns True to abort.
        refresh_url_callback: Optional callable() -> str. Re-resolves the
                               stream URL from the original page (token +
                               expires regenerated). Used when segments start
                               returning HTTP 403 because the token embedded
                               in the playlist URL expired mid-download —
                               typically on long (movie-length) videos that
                               take longer to download than the token's TTL.

    Returns:
        True on success, False on failure or cancellation.
    """
    print("\n[Transcoder] Starting transcode...")
    print(f"  Source : {source_url}")
    print(f"  Output : {output_path}")

    if progress_callback is None:
        progress_callback = _default_progress

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    temp_dir = os.path.join(APP_DIR, f"_temp_hls_{os.getpid()}")
    os.makedirs(temp_dir, exist_ok=True)

    session = requests.Session()
    session.headers.update(parse_headers_str(headers))

    # Reuse TCP/TLS connections across all segment downloads. Over a VPN the
    # per-request handshake latency dominates, so a large keep-alive pool that
    # matches the worker count gives the biggest speed-up. Without this the
    # default pool (10) is smaller than the worker count and connections get
    # torn down and re-opened constantly.
    _adapter = requests.adapters.HTTPAdapter(
        pool_connections=SEGMENT_WORKERS,
        pool_maxsize=SEGMENT_WORKERS,
        max_retries=0,
    )
    session.mount("https://", _adapter)
    session.mount("http://", _adapter)

    # Apply proxy from config so blocked CDN hosts are reachable.
    try:
        from config import load_config, apply_proxy as _apply_proxy
        _apply_proxy(session, load_config().get("proxy", ""))
    except Exception as _e:
        print(f"[Transcoder] Could not apply proxy from config: {_e}")

    try:
        # ------------------------------------------------------------------ #
        # Step 1 — Resolve master → media playlist                            #
        # ------------------------------------------------------------------ #
        if cancel_check and cancel_check():
            return False

        # max_attempts=1: fail fast so CDN-edge rotation below triggers
        # immediately rather than hammering the same blocked host 3× first.
        playlist_url, lines = _resolve_playlist(session, source_url, cancel_check=cancel_check,
                                                 max_attempts=1)
        if playlist_url is None:
            # The CDN edge encoded in this particular URL (e.g. a specific
            # "media-NNN" host) may simply be unreachable/down right now —
            # retrying the exact same host endlessly won't help. If we have
            # a way to re-resolve the stream from the original page, do so:
            # it typically hands back a different CDN edge.
            if refresh_url_callback:
                from urllib.parse import urlparse, parse_qs
                prev_host = urlparse(source_url).netloc
                prev_token = parse_qs(urlparse(source_url).query).get("token", [None])[0]
                print(f"[Transcoder] Playlist not reachable ({prev_host}). "
                      f"Requesting a fresh stream link (new token / CDN edge)...")
                for attempt in range(5):  # more attempts — each is cheap now
                    if cancel_check and cancel_check():
                        return False
                    try:
                        new_source_url = refresh_url_callback()
                    except Exception as e:
                        print(f"[Transcoder] Could not get a fresh stream URL: {e}")
                        new_source_url = None
                    if not new_source_url:
                        break
                    new_parsed = urlparse(new_source_url)
                    new_host = new_parsed.netloc
                    new_token = parse_qs(new_parsed.query).get("token", [None])[0]
                    # Accept the refreshed URL if EITHER the host OR the token
                    # changed. Ephemeral tokens (e.g. vixcloud) expire within
                    # seconds, so the same host with a brand-new token is a
                    # perfectly valid retry, not a dead edge.
                    if new_source_url == source_url or (new_host == prev_host and new_token == prev_token):
                        print(f"[Transcoder] Refreshed URL unchanged ({new_host}), retrying...")
                        time.sleep(1.5)
                        continue
                    print(f"[Transcoder] New source (host={new_host}, token changed={new_token != prev_token}): {new_source_url}")
                    # Full retry resilience now that we have a fresh link.
                    playlist_url, lines = _resolve_playlist(session, new_source_url,
                                                            cancel_check=cancel_check, max_attempts=3)
                    if playlist_url is not None:
                        break
                    prev_host, prev_token = new_host, new_token  # this link is also dead; skip it too
                    time.sleep(2.0)

            if playlist_url is None:
                print("[Transcoder Error] Could not reach any CDN edge for this stream.")
                return False

        # ------------------------------------------------------------------ #
        # Step 2 — Parse playlist: collect segments and encryption keys       #
        # ------------------------------------------------------------------ #
        local_lines, segments, keys = _parse_playlist(lines, playlist_url)

        # ------------------------------------------------------------------ #
        # Step 3 — Download encryption keys                                   #
        # ------------------------------------------------------------------ #
        if not _download_keys(session, keys, temp_dir, cancel_check):
            return False

        if not segments:
            print("[Transcoder Error] No segments found in playlist.")
            return False

        # Write the rewritten local playlist
        local_playlist_path = os.path.join(temp_dir, "local.m3u8")
        with open(local_playlist_path, "w", encoding="utf-8") as f:
            f.write("\n".join(local_lines) + "\n")

        # ------------------------------------------------------------------ #
        # Step 4 — Parallel segment download                                  #
        # ------------------------------------------------------------------ #
        if not _download_segments(session, segments, temp_dir, progress_callback, cancel_check,
                                   refresh_url_callback=refresh_url_callback):
            return False

        if cancel_check and cancel_check():
            return False

        # ------------------------------------------------------------------ #
        # Step 5 — FFmpeg transcode                                           #
        # ------------------------------------------------------------------ #
        return _run_ffmpeg(local_playlist_path, output_path, progress_callback, cancel_check)

    except Exception as e:
        print(f"\n[Transcoder Error] {e}")
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_with_retry(session, url, timeout=15, max_attempts=5, cancel_check=None, label="playlist"):
    """GET a URL with retries + exponential backoff, same resilience as
    segment/key downloads. Connect/DNS timeouts on the master/variant
    playlist used to abort the whole job instantly with zero retries —
    this is what was causing some episodes/movies to fail outright.

    `timeout` is used as the *read* timeout; the connect timeout is capped
    at 8 s regardless, so a dead CDN host is detected quickly instead of
    blocking for the full timeout value per attempt.
    """
    connect_timeout = min(8, timeout)
    effective_timeout = (connect_timeout, timeout)
    last_exc = None
    for attempt in range(max_attempts):
        if cancel_check and cancel_check():
            return None
        try:
            r = session.get(url, timeout=effective_timeout)
            if r.status_code == 200:
                return r
            print(f"[Transcoder] {label} fetch HTTP {r.status_code}, retrying ({attempt + 1}/{max_attempts})...")
        except Exception as e:
            last_exc = e
            print(f"[Transcoder] {label} fetch attempt {attempt + 1}/{max_attempts} failed: {e}")
        if attempt < max_attempts - 1:
            time.sleep(min(1.5 * (2 ** attempt), 8.0))
    if last_exc:
        print(f"[Transcoder Error] {label} fetch failed after {max_attempts} attempts: {last_exc}")
    return None


def _resolve_playlist(session, source_url, cancel_check=None, max_attempts=3, timeout=10):
    """Return (playlist_url, stripped_lines).  Follows master → media.

    max_attempts=1 is intentional when called for the initial fetch: we want
    to fail fast so the outer CDN-edge-rotation loop in transcode_to_psp can
    request a fresh stream URL (different CDN host) instead of retrying the
    same blocked edge 3 times. The caller passes max_attempts=3 explicitly
    when it already has a fresh URL and wants full retry resilience.
    """
    try:
        r = _get_with_retry(session, source_url, timeout=timeout, max_attempts=max_attempts,
                             cancel_check=cancel_check, label="Playlist")
        if r is None:
            return None, None

        lines = [l.strip() for l in r.text.splitlines() if l.strip()]

        # Check for a master playlist (contains STREAM-INF)
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
                variant_url = urljoin(source_url, lines[i + 1])
                print(f"[Transcoder] Variant playlist: {variant_url}")
                r2 = _get_with_retry(session, variant_url, timeout=timeout, max_attempts=max_attempts,
                                      cancel_check=cancel_check, label="Variant playlist")
                if r2 is None:
                    return None, None
                return variant_url, [l.strip() for l in r2.text.splitlines() if l.strip()]

        return source_url, lines
    except Exception as e:
        print(f"[Transcoder Error] _resolve_playlist: {e}")
        return None, None


def _parse_playlist(lines, playlist_url):
    """
    Walk through playlist lines, collecting segments and keys.

    Returns:
        local_lines: Lines for the rewritten local playlist.
        segments:    List of (url, local_filename) tuples.
        keys:        List of (url, local_filename) tuples.
    """
    local_lines = []
    segments = []
    keys = []
    key_counter = 0

    for line in lines:
        if not line.startswith("#"):
            # Segment
            seg_url = urljoin(playlist_url, line)
            local_name = f"seg_{len(segments):06d}.ts"
            segments.append((seg_url, local_name))
            local_lines.append(local_name)
        elif line.startswith("#EXT-X-KEY"):
            m = re.search(r'URI=["\']?([^"\'>,]+)["\']?', line)
            if m:
                key_url = urljoin(playlist_url, m.group(1))
                local_name = f"key_{key_counter}.key"
                keys.append((key_url, local_name))
                key_counter += 1
                new_line = line.replace(m.group(0), f'URI="{local_name}"')
                local_lines.append(new_line)
            else:
                local_lines.append(line)
        else:
            local_lines.append(line)

    return local_lines, segments, keys


def _download_keys(session, keys, temp_dir, cancel_check):
    """Download HLS encryption keys.  Returns False on failure."""
    for key_url, local_name in keys:
        if cancel_check and cancel_check():
            return False
        print(f"[Transcoder] Downloading key: {key_url}")
        key_path = os.path.join(temp_dir, local_name)
        for attempt in range(5):
            if cancel_check and cancel_check():
                return False
            try:
                r = session.get(key_url, timeout=30)
                if r.status_code == 200:
                    with open(key_path, "wb") as f:
                        f.write(r.content)
                    break
            except Exception as e:
                print(f"[Transcoder] Key download attempt {attempt + 1} failed: {e}")
            if attempt < 4:
                time.sleep(min(1.5 * (2 ** attempt), 8.0))
        else:
            print(f"[Transcoder Error] Could not download key: {key_url}")
            return False
    return True


def _download_segments(session, segments, temp_dir, progress_callback, cancel_check, refresh_url_callback=None):
    """Download all HLS segments in parallel.  Returns False on failure.

    Mirrors the timeout/retry resilience used by StreamFlix's NetworkClient
    (30s timeouts, no fail-fast on a single transient error) plus a serial
    retry pass for any segment that still fails after the parallel attempt,
    instead of aborting the whole job over one bad segment.

    Additionally handles HTTP 403 responses, which on these providers mean
    the token embedded in the playlist/segment URLs has expired mid-download
    (common on long, movie-length videos) rather than a transient network
    error. In that case retrying the same URL is pointless — instead, when
    a 403 cluster is detected, the stream is re-resolved from the original
    page via `refresh_url_callback` (fresh token) and the new segment URLs
    are substituted in for everything that hasn't downloaded yet.
    """
    total = len(segments)
    print(f"[Transcoder] Downloading {total} segments...")

    lock = threading.Lock()
    downloaded = [0]
    cancelled = [False]

    # local_name -> current url (mutated on refresh)
    current_url = {name: url for url, name in segments}
    # Guards against many threads trying to refresh the token at once.
    refresh_lock = threading.Lock()
    refresh_state = {"version": 0}

    def do_refresh(my_version):
        """Re-resolve the stream and remap remaining segment URLs by position.
        Only one thread actually performs the refresh per token-expiry event;
        the rest detect the version bump and just retry with the new URLs."""
        if not refresh_url_callback:
            return False
        with refresh_lock:
            if refresh_state["version"] != my_version:
                # Someone else already refreshed since this thread grabbed its URL.
                return True
            print("[Transcoder] Segment token appears expired (HTTP 403). Re-resolving stream URL...")
            try:
                new_source_url = refresh_url_callback()
            except Exception as e:
                print(f"[Transcoder] Could not refresh stream URL: {e}")
                return False
            if not new_source_url:
                print("[Transcoder] Refresh callback returned no URL.")
                return False

            new_playlist_url, new_lines = _resolve_playlist(session, new_source_url, cancel_check=cancel_check)
            if new_playlist_url is None:
                print("[Transcoder] Could not re-fetch playlist after refresh.")
                return False
            _, new_segments, _ = _parse_playlist(new_lines, new_playlist_url)

            if len(new_segments) != total:
                print(f"[Transcoder] Refreshed playlist segment count mismatch "
                      f"({len(new_segments)} vs {total}); refresh aborted.")
                return False

            # Segments are generated in the same order every time (seg_000000.ts, ...),
            # so position == identity. Swap in the fresh, still-valid URLs.
            with lock:
                for new_url, local_name in new_segments:
                    current_url[local_name] = new_url
                refresh_state["version"] += 1
            print("[Transcoder] Stream URL refreshed successfully, resuming download.")
            return True

    def fetch_segment(local_name, seg_path, max_attempts=5):
        """Try to download a single segment with exponential backoff.
        Refreshes the token once if the server starts returning 403s."""
        attempted_refresh = False
        for attempt in range(max_attempts):
            if cancel_check and cancel_check():
                cancelled[0] = True
                return False
            url = current_url[local_name]
            try:
                r = session.get(url, timeout=(8, 25))
                if r.status_code == 200 and r.content:
                    with open(seg_path, "wb") as f:
                        f.write(r.content)
                    return True
                if r.status_code == 403:
                    print(f"[Transcoder] Segment HTTP 403 (expired token?), attempt {attempt + 1}/{max_attempts}")
                    if not attempted_refresh:
                        attempted_refresh = True
                        do_refresh(refresh_state["version"])  # may update current_url[local_name]
                        continue  # retry immediately with the (possibly) refreshed url
                else:
                    print(f"[Transcoder] Segment HTTP {r.status_code}, retrying ({attempt + 1}/{max_attempts})...")
            except Exception as e:
                print(f"[Transcoder] Segment attempt {attempt + 1}/{max_attempts} failed: {e}")

            if attempt < max_attempts - 1:
                time.sleep(min(0.5 * (2 ** attempt), 3.0))
        return False

    def download_one(local_name):
        if cancel_check and cancel_check():
            cancelled[0] = True
            return None

        seg_path = os.path.join(temp_dir, local_name)
        ok = fetch_segment(local_name, seg_path)
        if ok:
            with lock:
                downloaded[0] += 1
                progress_callback((downloaded[0] / total) * 85.0)
            return True
        return False

    failed_segments = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=SEGMENT_WORKERS) as executor:
        futures = {
            executor.submit(download_one, name): name
            for _, name in segments
        }
        for future in concurrent.futures.as_completed(futures):
            if cancelled[0]:
                print("\n[Transcoder] Cancelled.")
                return False
            if future.result() is not True:
                failed_segments.append(futures[future])

    # Serial retry pass for whatever didn't make it through the parallel
    # pass, instead of aborting the entire download over one bad segment.
    if failed_segments:
        print(f"[Transcoder] Retrying {len(failed_segments)} segment(s) that failed initially...")
        still_failed = []
        for local_name in failed_segments:
            if cancel_check and cancel_check():
                print("\n[Transcoder] Cancelled.")
                return False
            seg_path = os.path.join(temp_dir, local_name)
            if fetch_segment(local_name, seg_path, max_attempts=5):
                with lock:
                    downloaded[0] += 1
                    progress_callback((downloaded[0] / total) * 85.0)
            else:
                still_failed.append(local_name)

        if still_failed:
            print(f"\n[Transcoder Error] {len(still_failed)} segment(s) could not be downloaded after retries.")
            return False

    progress_callback(90.0)
    return True


def _run_ffmpeg(local_playlist_path, output_path, progress_callback, cancel_check):
    """Run ffmpeg on the local playlist and stream progress updates."""
    print("[Transcoder] Starting FFmpeg conversion...")

    cmd = [
        _get_ffmpeg(), "-y", "-nostdin",
        "-protocol_whitelist", "file,crypto",
        "-allowed_extensions", "ALL",
        "-i", local_playlist_path,
        *_build_encode_args(),
        output_path,
    ]

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1,
        creationflags=_NO_WINDOW,
    )

    duration_seconds = 0.0
    ffmpeg_log = []

    while True:
        if cancel_check and cancel_check():
            try:
                process.kill()
            except Exception:
                pass
            print("\n[Transcoder] Conversion cancelled.")
            return False

        line = process.stderr.readline()
        if not line:
            break

        ffmpeg_log.append(line)

        if not duration_seconds:
            m = re.search(r"Duration:\s*(\d{2}:\d{2}:\d{2}\.\d{2})", line)
            if m:
                duration_seconds = parse_time(m.group(1))

        m = re.search(r"time=\s*(\d{2}:\d{2}:\d{2}\.\d{2})", line)
        if m and duration_seconds > 0:
            pct = min(10.0, (parse_time(m.group(1)) / duration_seconds) * 10.0)
            progress_callback(90.0 + pct)

    process.wait()

    if process.returncode == 0:
        progress_callback(100.0)
        print("\n[Transcoder] Done!")
        return True

    print(f"\n[Transcoder] FFmpeg exited with code {process.returncode}")
    print("--- FFmpeg output (last 50 lines) ---")
    print("".join(ffmpeg_log[-50:]))
    print("-------------------------------------")
    return False