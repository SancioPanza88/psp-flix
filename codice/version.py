"""
Gestione versione e controllo aggiornamenti per Streamflix PSP Suite.

Come funziona:
- L'app ha un numero di versione locale (APP_VERSION).
- Quando pubblichi una nuova versione su GitHub, crei una "Release" con un
  tag (es: v1.1.0). Basta quello: niente server, niente account da pagare.
- All'avvio l'app chiede all'API pubblica di GitHub qual è l'ultima release
  del tuo repository e la confronta con APP_VERSION.
- Se è diversa, avvisa l'utente con un popup (GUI) o un messaggio (CLI),
  con link alla pagina della release, cosi' chi ha problemi sa subito se
  sta usando una versione vecchia.

Per attivarlo basta scrivere il tuo repo qui sotto, tipo:
    GITHUB_REPO = "tuonomeutente/streamflix-psp"
"""

import json
import urllib.request
import urllib.error

APP_VERSION = "1.0.3"

# <-- IMPOSTA QUI il tuo repository GitHub, formato "utente/nome-repo"
GITHUB_REPO = "SancioPanza88/psp-flix"

_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_TIMEOUT = 6


def _version_tuple(v: str):
    v = v.strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def check_for_update():
    """Controlla su GitHub se esiste una versione più recente.

    Ritorna un dict {"available": bool, "latest": str, "url": str} oppure
    None se il controllo non è riuscito (no internet, repo non
    configurato, rate limit, ecc). Non lancia mai eccezioni: in caso di
    problemi torna semplicemente None, così l'app non si blocca mai per
    colpa del controllo aggiornamenti.
    """
    if not GITHUB_REPO or "tuonomeutente" in GITHUB_REPO:
        return None

    try:
        req = urllib.request.Request(
            _API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "streamflix-psp-update-check"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    latest_tag = data.get("tag_name", "")
    release_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")
    if not latest_tag:
        return None

    available = _version_tuple(latest_tag) > _version_tuple(APP_VERSION)
    return {"available": available, "latest": latest_tag, "url": release_url}
