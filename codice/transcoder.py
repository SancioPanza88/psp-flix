import concurrent.futures
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import requests
from urllib.parse import urljoin, urlsplit

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


def _get_ffprobe():
    """Restituisce il percorso di ffprobe (accanto all'exe se presente)."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = APP_DIR
    local = os.path.join(base, "ffprobe.exe")
    return local if os.path.exists(local) else "ffprobe"


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

# Number of HLS segments downloaded in parallel. Kept deliberately modest:
# these CDN hosts throttle aggressively when hammered (timeouts, resets,
# 403s that look like expired tokens). 16 workers triggered rate-limiting
# on movie-length downloads (~3000 segments for video+audio); 8 is still
# far faster than the transcode step while staying under the radar.
# The session's connection pool is sized to match this.
SEGMENT_WORKERS = 8

# Last human-readable failure reason (for the GUI to display instead of a
# generic "FFmpeg error"). Reset at the start of every transcode_to_psp call.
_last_error = ""


def get_last_error():
    """Return the failure reason of the last transcode ("" if none/OK)."""
    return _last_error


def _fail(msg):
    """Record a failure reason, log it, and return False."""
    global _last_error
    _last_error = msg
    print(f"[Transcoder Error] {msg}")
    return False

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
# PSP-safe filenames
# ---------------------------------------------------------------------------
# La XMB della PSP mostra "Dati danneggiati / Dati incompatibili" anche per
# file perfettamente validi se il nome contiene spazi, accenti o caratteri
# speciali, oppure se è troppo lungo. Questa è la causa più comune di file
# "incompatibili" con encode per il resto corretto: la GUI prima toglieva
# solo i caratteri vietati da Windows (\/:*?"<>|) lasciando tutto il resto
# (accenti, apostrofi, spazi, titoli da 100+ caratteri).
#
# Queste helper normalizzano qualunque titolo in un basename ASCII sicuro,
# corto e deterministico (stesso input -> stesso output, così la GUI e il
# transcoder non divergono mai sul nome del file finale).

_PSP_SAFE_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
)
_PSP_MAX_BASENAME = 48


def sanitize_psp_basename(title):
    """Riduce un titolo arbitrario a un basename ASCII PSP-safe.

    Es: "L'attacco dei Giganti: S01E01 – L'episodio..." -> "L_attacco_dei_Giganti_S01E01_L_episodio..."
    """
    if not title:
        return "video"
    norm = unicodedata.normalize("NFKD", str(title))
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    chars = []
    for ch in ascii_only:
        if ch in _PSP_SAFE_CHARS:
            chars.append(ch)
        elif ch.isspace() or ch in ("'", "`", '"', ":", ";", ",", "!", "?",
                                    "(", ")", "[", "]", "{", "}", "&", "+",
                                    "=", "@", "#", "$", "%", "^", "~", "|",
                                    "<", ">", "*", "/", "\\"):
            chars.append("_")
        # tutti gli altri caratteri vengono scartati
    safe = re.sub(r"_+", "_", "".join(chars)).strip("._")
    if not safe:
        safe = "video"
    if len(safe) > _PSP_MAX_BASENAME:
        safe = safe[:_PSP_MAX_BASENAME].rstrip("._") or "video"
    return safe


def sanitize_psp_filename(filename):
    """Versione per filename completi (basename + estensione).

    Normalizza l'estensione in .mp4 / .THM secondo convenzione PSP,
    sanifica il basename con sanitize_psp_basename().
    """
    base, ext = os.path.splitext(filename)
    low = ext.lower()
    if low == ".mp4":
        ext = ".mp4"
    elif low == ".thm":
        ext = ".THM"
    return sanitize_psp_basename(base) + ext


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

    # Difesa in profondità: anche se il chiamante passa un titolo con
    # accenti/spazi/caratteri speciali, il file scritto su disco ha sempre
    # un nome PSP-safe (la GUI sanifica già con la stessa funzione, quindi
    # questo è normalmente un no-op idempotente).
    directory, filename = os.path.split(output_path)
    safe_name = sanitize_psp_filename(filename)
    if safe_name != filename:
        print(f"[Transcoder] Filename PSP-safe: '{filename}' -> '{safe_name}'")
        output_path = os.path.join(directory, safe_name) if directory else safe_name
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

    global _last_error
    _last_error = ""

    try:
        # ------------------------------------------------------------------ #
        # Step 1 — Resolve master → media playlist(s)                         #
        # ------------------------------------------------------------------ #
        if cancel_check and cancel_check():
            return False

        # max_attempts=1: fail fast so CDN-edge rotation below triggers
        # immediately rather than hammering the same blocked host 3× first.
        resolved = _resolve_playlist(session, source_url, cancel_check=cancel_check,
                                     max_attempts=1)
        if resolved[0] is None:
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
                    resolved = _resolve_playlist(session, new_source_url,
                                                 cancel_check=cancel_check, max_attempts=3)
                    if resolved[0] is not None:
                        break
                    prev_host, prev_token = new_host, new_token  # this link is also dead; skip it too
                    time.sleep(2.0)

            if resolved[0] is None:
                return _fail("Rete irraggiungibile: nessun CDN risponde per questo stream. "
                             "Se usi un ISP che blocca questi host, serve una VPN/proxy "
                             "(vedi README, campo \"proxy\" in psp_config.json).")

        playlist_url, video_lines, audio_url, audio_lines = resolved
        has_audio = audio_url is not None and audio_lines is not None

        # ------------------------------------------------------------------ #
        # Step 2 — Parse playlist(s): collect segments and encryption keys    #
        # ------------------------------------------------------------------ #
        video_local, video_segs, video_keys = _parse_playlist(
            video_lines, playlist_url, prefix="v", key_prefix="vk")
        audio_local, audio_segs, audio_keys = ([], [], [])
        if has_audio:
            audio_local, audio_segs, audio_keys = _parse_playlist(
                audio_lines, audio_url, prefix="a", key_prefix="ak")

        all_segments = video_segs + audio_segs
        all_keys = video_keys + audio_keys

        # ------------------------------------------------------------------ #
        # Step 3 — Download encryption keys                                   #
        # ------------------------------------------------------------------ #
        if not _download_keys(session, all_keys, temp_dir, cancel_check):
            return _fail("Chiave di decifratura non scaricabile (CDN/host irraggiungibile).")

        if not video_segs:
            return _fail("Playlist senza segmenti video: stream non valido o scaduto.")

        # Write the rewritten local playlist(s). Always terminate with
        # #EXT-X-ENDLIST: the origin playlists are VOD but carry no ENDLIST
        # tag, and without it ffmpeg's HLS demuxer treats the input as a LIVE
        # event — stalling between segments until the transcode crawls at a
        # fraction of realtime (this was the "movies slow down" bug: hundreds
        # of segments x live-poll stall each).
        video_playlist_path = os.path.join(temp_dir, "video.m3u8")
        with open(video_playlist_path, "w", encoding="utf-8") as f:
            f.write("\n".join(_with_endlist(video_local)) + "\n")
        ffmpeg_inputs = [video_playlist_path]
        if has_audio and audio_segs:
            audio_playlist_path = os.path.join(temp_dir, "audio.m3u8")
            with open(audio_playlist_path, "w", encoding="utf-8") as f:
                f.write("\n".join(_with_endlist(audio_local)) + "\n")
            ffmpeg_inputs.append(audio_playlist_path)
            print(f"[Transcoder] Separate audio track: {len(audio_segs)} segments.")
        elif has_audio:
            print("[Transcoder] Audio playlist vuota, procedo solo video.")
            has_audio = False

        # ------------------------------------------------------------------ #
        # Step 4 — Parallel segment download                                  #
        # ------------------------------------------------------------------ #
        if not _download_segments(session, all_segments, temp_dir, progress_callback, cancel_check,
                                   refresh_url_callback=refresh_url_callback):
            # _download_segments already recorded the specific reason.
            return False

        if cancel_check and cancel_check():
            return False

        # ------------------------------------------------------------------ #
        # Step 4b — Completeness check before ffmpeg                          #
        # ------------------------------------------------------------------ #
        missing = [name for _, name in all_segments
                   if not os.path.exists(os.path.join(temp_dir, name))
                   or os.path.getsize(os.path.join(temp_dir, name)) == 0]
        if missing:
            return _fail(f"Download incompleto: {len(missing)} segmenti mancanti su "
                         f"{len(all_segments)} (CDN instabile o throttling). "
                         f"Riprova più tardi o con VPN/proxy.")

        # ------------------------------------------------------------------ #
        # Step 5 — FFmpeg transcode                                           #
        # ------------------------------------------------------------------ #
        if not _run_ffmpeg(ffmpeg_inputs, output_path, progress_callback, cancel_check):
            if not _last_error:
                return _fail("Conversione FFmpeg fallita (vedi log qui sopra).")
            return False

        # ------------------------------------------------------------------ #
        # Step 6 — THM + verifica finale (non fatali)                         #
        # ------------------------------------------------------------------ #
        # La PSP mostra l'anteprima solo se esiste un .THM con lo stesso nome
        # del video: ora viene creato in automatico, niente più tool separato
        # obbligatorio. Poi un controllo ffprobe verifica che il file sia
        # davvero nei limiti hardware PSP (codec, risoluzione, brand MSNV).
        try:
            create_psp_thumbnail(output_path)
        except Exception as e:
            print(f"[Transcoder] THM auto-creation failed (non-fatal): {e}")
        try:
            verify_psp_output(output_path)
        except Exception as e:
            print(f"[Transcoder] PSP verification failed (non-fatal): {e}")
        return True

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


def _pick_audio_uri(master_lines, master_url):
    """Return the URI of the preferred AUDIO rendition, or None.

    Prefers the DEFAULT=YES rendition (vixcloud marks the Italian track
    default when language=it was requested), else the first audio group.
    Subtitle groups are ignored (the PSP can't use them anyway).
    """
    fallback = None
    for line in master_lines:
        if not (line.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in line):
            continue
        m = re.search(r'URI="([^"]+)"', line)
        if not m:
            continue
        uri = urljoin(master_url, m.group(1))
        if fallback is None:
            fallback = uri
        if "DEFAULT=YES" in line:
            return uri
    return fallback


def _pick_variant_url(master_lines, master_url):
    """Return the URL of the cheapest video variant.

    Master playlists list e.g. 480p + 720p renditions: for a 480x272 PSP
    target the lowest BANDWIDTH is always the right choice (fewer bytes to
    download, faster to transcode, same final quality). Falls back to the
    first variant when no BANDWIDTH is advertised.
    """
    variants = []  # (bandwidth_or_None, url)
    for i, line in enumerate(master_lines):
        if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(master_lines):
            m = re.search(r"BANDWIDTH=(\d+)", line)
            bw = int(m.group(1)) if m else None
            variants.append((bw, urljoin(master_url, master_lines[i + 1])))
    if not variants:
        return None
    with_bw = [(bw, url) for bw, url in variants if bw is not None]
    if with_bw:
        best = min(with_bw, key=lambda t: t[0])
        print(f"[Transcoder] Variants: {len(variants)}, picking lowest bandwidth ({best[0] // 1000}k).")
        return best[1]
    return variants[0][1]


def _resolve_playlist(session, source_url, cancel_check=None, max_attempts=3, timeout=10):
    """Return (video_url, video_lines, audio_url, audio_lines).

    Follows master → media playlists. Handles two master shapes:
      * classic: variants are muxed (video+audio) → audio_* are None;
      * vixcloud-style: video-only variants + separate EXT-X-MEDIA AUDIO
        rendition(s) → both are fetched so the final MP4 has sound.

    On failure returns (None, None, None, None).

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
            return None, None, None, None

        lines = [l.strip() for l in r.text.splitlines() if l.strip()]

        # Media playlist straight away (no STREAM-INF)?
        variant_url = _pick_variant_url(lines, source_url)
        if variant_url is None:
            return source_url, lines, None, None

        audio_url = _pick_audio_uri(lines, source_url)
        print(f"[Transcoder] Variant playlist: {variant_url}")
        r2 = _get_with_retry(session, variant_url, timeout=timeout, max_attempts=max_attempts,
                              cancel_check=cancel_check, label="Variant playlist")
        if r2 is None:
            return None, None, None, None
        video_lines = [l.strip() for l in r2.text.splitlines() if l.strip()]

        audio_lines = None
        if audio_url:
            print(f"[Transcoder] Audio playlist: {audio_url}")
            r3 = _get_with_retry(session, audio_url, timeout=timeout, max_attempts=max_attempts,
                                  cancel_check=cancel_check, label="Audio playlist")
            if r3 is None:
                print("[Transcoder] Audio playlist unreachable, continuing video-only.")
                audio_url = None
            else:
                audio_lines = [l.strip() for l in r3.text.splitlines() if l.strip()]

        return variant_url, video_lines, audio_url, audio_lines
    except Exception as e:
        print(f"[Transcoder Error] _resolve_playlist: {e}")
        return None, None, None, None


def _parse_playlist(lines, playlist_url, prefix="seg", key_prefix="key"):
    """
    Walk through playlist lines, collecting segments and keys.

    `prefix`/`key_prefix` namespace the local filenames so video and audio
    tracks can share one temp dir (v_000000.ts vs a_000000.ts).

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
            local_name = f"{prefix}_{len(segments):06d}.ts"
            segments.append((seg_url, local_name))
            local_lines.append(local_name)
        elif line.startswith("#EXT-X-KEY"):
            m = re.search(r'URI=["\']?([^"\'>,]+)["\']?', line)
            if m:
                key_url = urljoin(playlist_url, m.group(1))
                local_name = f"{key_prefix}_{key_counter}.key"
                keys.append((key_url, local_name))
                key_counter += 1
                new_line = line.replace(m.group(0), f'URI="{local_name}"')
                local_lines.append(new_line)
            else:
                local_lines.append(line)
        else:
            local_lines.append(line)

    return local_lines, segments, keys


def _seg_identity(url):
    """Stable segment identity across token refreshes: path basename only
    (query tokens change, the CDN path doesn't)."""
    try:
        path = urlsplit(url).path
    except Exception:
        path = url.split("?", 1)[0]
    return path.rsplit("/", 1)[-1] or url


def _with_endlist(local_lines):
    """Append #EXT-X-ENDLIST unless already present (see caller comment)."""
    if any(l.strip() == "#EXT-X-ENDLIST" for l in local_lines):
        return local_lines
    return local_lines + ["#EXT-X-ENDLIST"]


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

    Resilience notes (learned the hard way on these CDNs):
      * Only 8 parallel workers + staggered start: hammering with 16
        triggered server-side throttling (timeouts/resets/403s) halfway
        through movie-length downloads (~3000 segments with audio).
      * HTTP 403 usually means the token embedded in the segment URLs
        expired mid-download → re-resolve from the original page and swap
        in fresh URLs, matched by path identity (robust to reordering and
        count changes), not by position.
      * Timeouts Resets get long jittered backoff instead of fail-fast:
        the CDN tarpits under load and recovers a few seconds later.
      * A serial retry pass mops up whatever the parallel pass missed
        instead of aborting the whole job over a few bad segments.
    """
    total = len(segments)
    print(f"[Transcoder] Downloading {total} segments ({SEGMENT_WORKERS} workers)...")

    lock = threading.Lock()
    downloaded = [0]
    cancelled = [False]

    # local_name -> current url (mutated on refresh)
    current_url = {name: url for url, name in segments}
    # Guards against many threads trying to refresh the token at once.
    refresh_lock = threading.Lock()
    refresh_state = {"version": 0}

    def do_refresh(my_version):
        """Re-resolve the stream and remap remaining segment URLs.
        Only one thread actually performs the refresh per token-expiry event;
        the rest detect the version bump and just retry with the new URLs.
        Matching is by path identity (CDN path is stable, query tokens are
        not), so reordered or resized playlists still map correctly."""
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

            new_resolved = _resolve_playlist(session, new_source_url, cancel_check=cancel_check)
            if new_resolved[0] is None:
                print("[Transcoder] Could not re-fetch playlist after refresh.")
                return False
            new_playlist_url, new_video_lines = new_resolved[0], new_resolved[1]
            new_audio_url, new_audio_lines = new_resolved[2], new_resolved[3]
            _, new_video_segs, _ = _parse_playlist(new_video_lines, new_playlist_url,
                                                   prefix="v", key_prefix="vk")
            new_all = list(new_video_segs)
            if new_audio_url and new_audio_lines:
                _, new_audio_segs, _ = _parse_playlist(new_audio_lines, new_audio_url,
                                                       prefix="a", key_prefix="ak")
                new_all.extend(new_audio_segs)

            by_identity = {}
            for new_url, _new_name in new_all:
                by_identity.setdefault(_seg_identity(new_url), new_url)

            remapped = 0
            with lock:
                for local_name, old_url in current_url.items():
                    fresh = by_identity.get(_seg_identity(old_url))
                    if fresh is not None and fresh != old_url:
                        current_url[local_name] = fresh
                        remapped += 1
                refresh_state["version"] += 1
            if remapped:
                print(f"[Transcoder] Stream URL refreshed, {remapped} segment URL(s) updated.")
            else:
                print("[Transcoder] Refresh returned identical URLs; retrying anyway.")
            # Small pause so all workers don't hammer the fresh URLs at once.
            time.sleep(0.5)
            return True

    def fetch_segment(local_name, seg_path, max_attempts=6):
        """Try to download a single segment with jittered exponential backoff.
        Refreshes the token once if the server starts returning 403s."""
        attempted_refresh = False
        for attempt in range(max_attempts):
            if cancel_check and cancel_check():
                cancelled[0] = True
                return False
            url = current_url[local_name]
            try:
                r = session.get(url, timeout=(8, 40))
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
                elif r.status_code in (500, 502, 503, 504):
                    print(f"[Transcoder] Segment HTTP {r.status_code} (server busy), backing off ({attempt + 1}/{max_attempts})...")
                else:
                    print(f"[Transcoder] Segment HTTP {r.status_code}, retrying ({attempt + 1}/{max_attempts})...")
            except Exception as e:
                print(f"[Transcoder] Segment attempt {attempt + 1}/{max_attempts} failed: {e}")

            if attempt < max_attempts - 1:
                time.sleep(min(1.0 * (2 ** attempt), 10.0) + random.uniform(0, 0.5))
        return False

    def download_one(local_name):
        if cancel_check and cancel_check():
            cancelled[0] = True
            return None

        # Stagger worker start to avoid a thundering-herd burst that the
        # CDN's rate limiter reads as an attack.
        time.sleep(random.uniform(0, 0.4))
        seg_path = os.path.join(temp_dir, local_name)
        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
            with lock:
                downloaded[0] += 1
                progress_callback((downloaded[0] / total) * 80.0)
            return True
        ok = fetch_segment(local_name, seg_path)
        if ok:
            with lock:
                downloaded[0] += 1
                progress_callback((downloaded[0] / total) * 80.0)
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
    # pass, instead of aborting the entire download over a few bad segments.
    if failed_segments:
        print(f"[Transcoder] Retrying {len(failed_segments)} segment(s) that failed initially...")
        still_failed = []
        for local_name in failed_segments:
            if cancel_check and cancel_check():
                print("\n[Transcoder] Cancelled.")
                return False
            seg_path = os.path.join(temp_dir, local_name)
            if fetch_segment(local_name, seg_path, max_attempts=6):
                with lock:
                    downloaded[0] += 1
                    progress_callback((downloaded[0] / total) * 80.0)
            else:
                still_failed.append(local_name)

        if still_failed:
            return _fail(f"{len(still_failed)} segmenti non scaricabili dopo tutti i retry "
                         f"(CDN instabile/throttling). Riprova più tardi o con VPN/proxy.")

    progress_callback(80.0)
    return True


def _run_ffmpeg(input_playlists, output_path, progress_callback, cancel_check):
    """Run ffmpeg on the local playlist(s) and stream progress updates.

    `input_playlists` is a list: [video.m3u8] for classic muxed streams,
    [video.m3u8, audio.m3u8] when the master carried a separate AUDIO
    rendition (vixcloud-style) — mapped explicitly and cut to the shortest
    track so A/V never drift apart.
    """
    if isinstance(input_playlists, str):
        input_playlists = [input_playlists]
    print(f"[Transcoder] Starting FFmpeg conversion ({len(input_playlists)} input(s))...")

    cmd = [_get_ffmpeg(), "-y", "-nostdin"]
    for pl in input_playlists:
        # NB: queste sono opzioni DI INPUT (demuxer/HLS): valgono solo per
        # l'-i che segue, quindi vanno ripetute davanti a OGNI input.
        # Senza allowed_extensions sul secondo input, ffmpeg rifiuta il file
        # .key ("blocked for security reasons") e l'audio fallisce.
        cmd += ["-fflags", "+discardcorrupt",
                "-protocol_whitelist", "file,crypto",
                "-allowed_extensions", "ALL",
                "-i", pl]
    if len(input_playlists) > 1:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += [
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
            pct = min(20.0, (parse_time(m.group(1)) / duration_seconds) * 20.0)
            progress_callback(80.0 + pct)

    process.wait()

    if process.returncode == 0:
        progress_callback(100.0)
        print("\n[Transcoder] Done!")
        return True

    print(f"\n[Transcoder] FFmpeg exited with code {process.returncode}")
    print("--- FFmpeg output (last 50 lines) ---")
    print("".join(ffmpeg_log[-50:]))
    print("-------------------------------------")
    tail = "".join(ffmpeg_log[-8:])
    return _fail(f"Conversione FFmpeg fallita (codice {process.returncode}). "
                 f"Dettagli nel log. Ultime righe: {tail.strip()[:300]}")


# ---------------------------------------------------------------------------
# Post-transcode: THM automatica + verifica compatibilità PSP
# ---------------------------------------------------------------------------

def _get_media_duration(path):
    """Durata in secondi via ffprobe, oppure None se non disponibile."""
    try:
        result = subprocess.run(
            [_get_ffprobe(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
            text=True, creationflags=_NO_WINDOW,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def create_psp_thumbnail(video_path):
    """Crea il file .THM (JPEG 160x120) accanto al video.

    La PSP, nel menu Video -> Memory Stick, mostra l'anteprima solo se
    esiste un file con stesso basename ed estensione .THM. Ritorna il
    percorso del .THM creato, oppure None se non è stato possibile.
    Non lancia mai eccezioni "normali": i fallimenti tornano None.
    """
    try:
        base, _ext = os.path.splitext(video_path)
        thm_path = base + ".THM"

        duration = _get_media_duration(video_path)
        if duration and duration > 10:
            seek = max(3.0, duration * 0.15)
        else:
            seek = 5.0

        # -f mjpeg è obbligatorio: ffmpeg non indovina il formato da .THM.
        for attempt_ss in (seek, 0):
            cmd = [
                _get_ffmpeg(), "-y", "-nostdin",
                "-ss", str(attempt_ss),
                "-i", video_path,
                "-vframes", "1",
                "-vf", "scale=160:120",
                "-q:v", "3",
                "-f", "mjpeg",
                thm_path,
            ]
            try:
                result = subprocess.run(
                    cmd, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=30, creationflags=_NO_WINDOW,
                )
            except Exception:
                result = None
            if result is not None and result.returncode == 0 \
                    and os.path.exists(thm_path) and os.path.getsize(thm_path) > 0:
                print(f"[Transcoder] Thumbnail created: {thm_path}")
                return thm_path
        print("[Transcoder] Could not extract thumbnail frame.")
        return None
    except Exception as e:
        print(f"[Transcoder] create_psp_thumbnail: {e}")
        return None


def verify_psp_output(path):
    """Verifica che il file finale rispetti i limiti hardware della PSP.

    Controlla (via ffprobe, se disponibile): brand MSNV, video H.264
    Baseline <= 480x272, audio AAC, sample rate 48000. Scrive un report
    nel log con eventuali WARNING. Non lancia eccezioni: serve solo a
    intercettare in anticipo file che la PSP rifiuterebbe.
    Ritorna True se tutti i controlli passano, False altrimenti.
    """
    problems = []
    try:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            print("[PSP-Check] FAIL: file mancante o vuoto.")
            return False

        ffprobe = _get_ffprobe()
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_format", "-show_streams",
                 "-of", "default=noprint_wrappers=1", path],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=20, text=True,
                creationflags=_NO_WINDOW,
            )
            info = result.stdout.lower()
        except FileNotFoundError:
            print("[PSP-Check] ffprobe non trovato, salto la verifica "
                  "(il file è stato comunque creato).")
            return True

        if "msnv" not in info:
            problems.append("brand MSNV assente (muxer non-PSP?)")
        if "codec_name=h264" not in info and "codec_name=avc" not in info:
            problems.append("video non H.264")
        if "baseline" not in info and "constrained baseline" not in info:
            problems.append("profilo video non Baseline")
        if "codec_name=aac" not in info:
            problems.append("audio non AAC")
        if "sample_rate=48000" not in info:
            problems.append("audio non a 48000 Hz")

        m_w = re.search(r"^width=(\d+)", info, re.M)
        m_h = re.search(r"^height=(\d+)", info, re.M)
        if m_w and m_h:
            w, h = int(m_w.group(1)), int(m_h.group(1))
            if w > 480 or h > 272:
                problems.append(f"risoluzione {w}x{h} oltre 480x272")

        size_mb = os.path.getsize(path) / (1024 * 1024)
        if problems:
            print(f"[PSP-Check] {os.path.basename(path)} ({size_mb:.1f} MB): "
                  f"WARNING — " + "; ".join(problems))
            return False
        print(f"[PSP-Check] {os.path.basename(path)} ({size_mb:.1f} MB): OK "
              f"per PSP (MSNV, H.264 Baseline <=480x272, AAC 48kHz). "
              f"Copiarlo con il .THM in Memory Stick/VIDEO/.")
        return True
    except Exception as e:
        print(f"[PSP-Check] verifica non riuscita: {e}")
        return False