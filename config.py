"""
Shared config loader for Streamflix PSP Suite.
Reads/writes psp_config.json.
"""
import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "psp_config.json")

_DEFAULTS = {
    "psp_video_dir": "",
    "auto_copy": False,
    "proxy": "",
}


def load_config() -> dict:
    cfg = dict(_DEFAULTS)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Config] Could not load {_CONFIG_PATH}: {e}")
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[Config] Could not save {_CONFIG_PATH}: {e}")


def apply_proxy(session, proxy_url: str) -> None:
    """Configura un requests.Session per passare attraverso un proxy locale.

    Pensata per l'uso con ProtonVPN (o qualsiasi altro proxy SOCKS5/HTTP):
    in psp_config.json imposta "proxy" con l'indirizzo del proxy locale,
    es:
        "proxy": "socks5://127.0.0.1:1080"
    oppure
        "proxy": "http://127.0.0.1:8080"

    Se "proxy" è vuoto (default), non fa nulla e la sessione usa la
    connessione di rete normale.
    """
    if not proxy_url:
        return
    try:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    except Exception as e:
        print(f"[Config] Could not apply proxy '{proxy_url}': {e}")