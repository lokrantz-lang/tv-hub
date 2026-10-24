"""Uppdatering av TV-appen.

GitHub-bygget lägger tvhub.apk och version.json på en fast adress.
Home Assistant hämtar dem och lämnar ut APK:n till boxarna i hemnätet.
"""
import json
import os
import threading
import time
import urllib.request

from .config import CFG

CHECK_EVERY_S = 30 * 60
_lock = threading.Lock()
_download_lock = threading.Lock()
MAX_APK_BYTES = 200 * 1024 * 1024
_cache = {"checked": 0.0, "latest": None, "error": None}


def _url(name):
    base = os.environ.get("APP_RELEASE_BASE")  # används av testerna
    if base:
        return f"{base.rstrip('/')}/{name}"
    repo = (CFG.get("app_repo") or "").strip().strip("/")
    return f"https://github.com/{repo}/releases/download/apk-latest/{name}" if repo else None


def _apk_path(version_code):
    return os.path.join(CFG["data_dir"], f"tvhub-{int(version_code)}.apk")


def latest(force=False):
    """{"version_code", "version_name"} eller None. Cachas en halvtimme."""
    with _lock:
        age = time.time() - _cache["checked"]
        if age < (20 if force else CHECK_EVERY_S):
            return _cache["latest"]
        _cache["checked"] = time.time()
        url = _url("version.json")
        if not url:
            _cache.update(latest=None, error="app_repo saknas i inställningarna")
            return None
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                data = json.loads(response.read(1_000_000).decode("utf-8"))
            _cache.update(latest={"version_code": int(data["versionCode"]),
                                  "version_name": str(data.get("versionName", ""))}, error=None)
        except Exception as exc:
            _cache["error"] = str(exc)
        return _cache["latest"]


def cached_latest():
    """Senast kända version utan nätverksanrop."""
    return _cache["latest"]


def status():
    info = latest()
    return {"latest": info, "error": _cache["error"], "repo": CFG.get("app_repo")}


def ensure_apk(version_code):
    """Laddar ner APK:n till /data om den inte redan finns. Returnerar sökvägen."""
    path = _apk_path(version_code)
    if os.path.exists(path):
        return path
    with _download_lock:  # en nedladdning åt gången; de som väntar får den färdiga filen
        if os.path.exists(path):
            return path
        url = _url("tvhub.apk")
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.part"
        total = 0
        try:
            with urllib.request.urlopen(url, timeout=120) as response, open(tmp, "wb") as fh:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_APK_BYTES:
                        raise IOError("Nedladdad APK är för stor")
                    fh.write(chunk)
            if os.path.getsize(tmp) < 100_000:
                raise IOError("Nedladdad APK är för liten")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    # Rensa äldre versioner.
    for name in os.listdir(CFG["data_dir"]):
        if name.startswith("tvhub-") and name.endswith(".apk") and name != os.path.basename(path):
            try:
                os.remove(os.path.join(CFG["data_dir"], name))
            except OSError:
                pass
    return path
