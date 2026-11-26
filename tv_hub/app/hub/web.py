"""Webbappen (tittande i webbläsaren på laptopen): strömmen går via tillägget.

Webbläsaren får aldrig portalens adress, användarnamn eller lösenord. Den spelar /web/stream/<session_id>, och
tillägget hämtar strömmen från Toucan och skickar vidare bytes. Sessionen räknas som vanligt (en ström åt
gången, prioritet, Lovisas låsta profil). session_id är slumpat (96 bitar) och gäller bara medan sessionen pågår.
"""
import re
import urllib.error
import urllib.request

from . import db, devices
from .config import CFG

WEB_MODEL = "Webbläsare"
CHUNK = 64 * 1024
SID = re.compile(r"^[0-9a-f]{24}$")


def is_web(device):
    return device is not None and device["model"] == WEB_MODEL


def _lookup(session_id):
    if not SID.match(session_id or ""):
        return None
    with devices._pin_lock_urls:
        entry = devices._recent_urls.get(session_id)
    if entry is None:
        return None
    row = db.q1("SELECT s.id, s.type, d.model FROM sessions s JOIN devices d ON d.id = s.device_id "
                "WHERE s.id = ? AND s.ended IS NULL", (session_id,))
    if row is None or row["model"] != WEB_MODEL:
        return None
    url, _adult, _portal, kind = entry
    if kind == "live":
        # Webbläsaren spelar MPEG-TS (mpegts.js), inte HLS-listor som pekar direkt på Toucan.
        url = re.sub(r"\.m3u8(\?.*)?$", ".ts", url)
    return url, kind


def stream(h, session_id):
    """Skickar strömmen vidare. Range stöds (filmer och avsnitt går att spola)."""
    found = _lookup(session_id)
    if found is None:
        h.send_response(404)
        h.send_header("Content-Length", "0")
        h.end_headers()
        return
    url, kind = found
    headers = {"User-Agent": CFG["user_agent"]}
    rng = h.headers.get("Range")
    if rng and kind in ("vod", "episode") and re.match(r"^bytes=\d*-\d*$", rng):
        headers["Range"] = rng
    try:
        upstream = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20)
    except urllib.error.HTTPError as exc:
        status = 416 if exc.code == 416 else 502
        h.send_response(status)
        h.send_header("Content-Length", "0")
        h.end_headers()
        return
    except (urllib.error.URLError, OSError):
        h.send_response(502)
        h.send_header("Content-Length", "0")
        h.end_headers()
        return
    try:
        status = upstream.status if upstream.status in (200, 206) else 200
        h.send_response(status)
        ctype = upstream.headers.get("Content-Type") or ""
        if kind in ("live", "catchup"):
            ctype = "video/mp2t"
        elif not ctype or "octet" in ctype or "text" in ctype:
            ctype = "video/mp4"
        h.send_header("Content-Type", ctype)
        for key in ("Content-Length", "Content-Range", "Accept-Ranges"):
            if upstream.headers.get(key):
                h.send_header(key, upstream.headers[key])
        if kind in ("vod", "episode") and not upstream.headers.get("Accept-Ranges"):
            h.send_header("Accept-Ranges", "bytes")
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        h.close_connection = True
        try:
            h.connection.settimeout(120)  # pausad video i webbläsaren: vänta en stund innan anslutningen släpps
        except OSError:
            pass
        while True:
            data = upstream.read(CHUNK)
            if not data:
                break
            h.wfile.write(data)
            # Stoppad eller övertagen session: sluta skicka.
            if not _still_active(session_id):
                break
    except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
        pass
    finally:
        upstream.close()


_checks = {}


def _still_active(session_id):
    """Kollar sessionen högst en gång i sekunden per ström."""
    import time
    now = time.monotonic()
    last, ok = _checks.get(session_id, (0, True))
    if now - last < 1:
        return ok
    row = db.q1("SELECT ended, stop_requested FROM sessions WHERE id = ?", (session_id,))
    ok = bool(row) and row["ended"] is None
    _checks[session_id] = (now, ok)
    if len(_checks) > 200:
        _checks.clear()
    return ok
