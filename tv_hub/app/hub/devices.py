"""Enheter (TV-boxar), parning, uppspelningssessioner och anslutningsvakt.

Toucan tillåter bara en ström åt gången. Vakten ser till att en box inte
startar en ström medan en annan box tittar, och att ett övertagande väntar
tills den andra boxen har stoppat.
"""
import hashlib
import json
import re
import secrets
import time

from . import catalog, db, portals
from .config import CFG

ACTIVE_WINDOW_S = 35        # session räknas som aktiv om heartbeat inom detta
TAKEOVER_TIMEOUT_S = 15     # max väntan på att den andra boxen stoppar
PAIR_CODE_TTL_MIN = 10
SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
SAFE_EXT = re.compile(r"^[A-Za-z0-9]{1,8}$")


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------- parning ----------
def new_pair_code():
    db.x("DELETE FROM pair_codes WHERE expires < ?", (db.iso(),))
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = db.later(minutes=PAIR_CODE_TTL_MIN)
    db.x("INSERT OR REPLACE INTO pair_codes (code, expires) VALUES (?, ?)", (code, expires))
    return {"code": code, "expires": expires}


def pair(code, name, model, app_version, ip):
    code = str(code or "").strip()
    row = db.q1("SELECT * FROM pair_codes WHERE code = ? AND expires >= ?", (code, db.iso()))
    if not row:
        return None
    db.x("DELETE FROM pair_codes WHERE code = ?", (code,))
    token = secrets.token_urlsafe(32)
    name = (str(name or "").strip() or "TV-box")[:40]
    device_id = db.x(
        """INSERT INTO devices (name, model, app_version, token_hash, created, last_seen, last_ip)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, str(model or "")[:60], str(app_version or "")[:20], _hash(token), db.iso(), db.iso(), ip),
    ).lastrowid
    db.log_event("paired", {"name": name, "model": model}, device_id=device_id)
    return {"token": token, "device_id": device_id, "name": name}


def create_token_device(name):
    """Skapar en enhet direkt (för Home Assistant-sensorer)."""
    token = secrets.token_urlsafe(32)
    device_id = db.x(
        "INSERT INTO devices (name, model, token_hash, created) VALUES (?, 'integration', ?, ?)",
        (name[:40], _hash(token), db.iso()),
    ).lastrowid
    return {"token": token, "device_id": device_id}


def authenticate(header, ip):
    if not header or not header.startswith("Bearer "):
        return None
    row = db.q1("SELECT * FROM devices WHERE token_hash = ?", (_hash(header[7:].strip()),))
    if row:
        db.x("UPDATE devices SET last_seen = ?, last_ip = ? WHERE id = ?", (db.iso(), ip, row["id"]))
    return row


def list_devices():
    out = []
    for row in db.q("SELECT id, name, model, app_version, created, last_seen, last_ip FROM devices ORDER BY id"):
        item = dict(row)
        session = active_session_for(row["id"])
        item["playing"] = session
        out.append(item)
    return out


def rename(device_id, name):
    name = (name or "").strip()[:40]
    if not name:
        raise ValueError("Namn saknas")
    db.x("UPDATE devices SET name = ? WHERE id = ?", (name, device_id))


def remove(device_id):
    db.x("UPDATE sessions SET ended = ?, end_reason = 'device_removed' WHERE device_id = ? AND ended IS NULL",
         (db.iso(), device_id))
    return db.x("DELETE FROM devices WHERE id = ?", (device_id,)).rowcount > 0


# ---------- sessioner ----------
def _active_rows(exclude_device=None):
    sql = """SELECT s.*, d.name AS device_name FROM sessions s JOIN devices d ON d.id = s.device_id
             WHERE s.ended IS NULL AND s.last_heartbeat >= ?"""
    args = [db.ago(seconds=ACTIVE_WINDOW_S)]
    if exclude_device is not None:
        sql += " AND s.device_id != ?"
        args.append(exclude_device)
    return db.q(sql, args)


def active_sessions():
    out = []
    for row in _active_rows():
        item = dict(row)
        item["metrics"] = json.loads(item["metrics"]) if item["metrics"] else None
        out.append(item)
    return out


def active_session_for(device_id):
    rows = [r for r in _active_rows() if r["device_id"] == device_id]
    if not rows:
        return None
    r = rows[0]
    return {"session_id": r["id"], "type": r["type"], "item_id": r["item_id"],
            "item_name": r["item_name"], "started": r["started"]}


def _end(session_id, reason):
    db.x("UPDATE sessions SET ended = ?, end_reason = ? WHERE id = ? AND ended IS NULL",
         (db.iso(), reason, session_id))


def request_play(device, kind, item_id, ext=None, force=False, exclude_portals=(), name=None):
    """Returnerar ett av:
    {"status": "conflict", ...}  en annan box tittar, fråga användaren
    {"status": "waiting", ...}   övertagande pågår, fråga igen om en sekund
    {"status": "ok", "url": ..., "session_id": ...}
    {"status": "error", "error": ...}
    """
    if kind not in ("live", "vod", "episode"):
        return {"status": "error", "error": "Okänd typ"}
    item_id = str(item_id)
    if not SAFE_ID.match(item_id):
        return {"status": "error", "error": "Ogiltigt id"}

    if (db.get_setting("server_test_until", 0) or 0) > time.time():
        return {"status": "waiting", "retry_ms": 2000,
                "message": "Servern kör ett kapacitetstest. Uppspelningen startar strax."}

    others = _active_rows(exclude_device=device["id"])
    if others:
        now_s = time.time()
        pending = [o for o in others if o["stop_requested"]]
        if not force and not pending:
            o = others[0]
            return {
                "status": "conflict",
                "conflict": {"device": o["device_name"], "item_name": o["item_name"], "since": o["started"]},
                "message": f"{o['device_name']} tittar redan. Toucan tillåter bara en ström åt gången.",
            }
        for o in others:
            if not o["stop_requested"]:
                db.x("UPDATE sessions SET stop_requested = ? WHERE id = ?", (int(now_s), o["id"]))
                db.log_event("takeover_requested", {"by": device["name"], "from": o["device_name"]},
                             device_id=device["id"], session_id=o["id"])
        still_waiting = False
        for o in _active_rows(exclude_device=device["id"]):
            requested = o["stop_requested"] or int(now_s)
            if now_s - requested >= TAKEOVER_TIMEOUT_S:
                _end(o["id"], "takeover_timeout")
            else:
                still_waiting = True
        if still_waiting:
            return {"status": "waiting", "retry_ms": 1000,
                    "message": "Väntar på att den andra boxen ska stoppa…"}

    portal = portals.best_portal(exclude=tuple(int(p) for p in exclude_portals if str(p).isdigit()))
    if portal is None:
        db.log_event("play_no_portal", {"type": kind, "id": item_id}, device_id=device["id"])
        return {"status": "error", "error": "Ingen portal fungerar just nu. Se Diagnostik i webbadminen."}

    client = portals.client(portal)
    if kind == "live":
        item = catalog.get_item("live", item_id)
        ext_final = portals.output_format(portal)
        display = item["name"] if item else (name or f"Kanal {item_id}")
    elif kind == "vod":
        item = catalog.get_item("vod", item_id)
        ext_final = (item and item["ext"]) or (ext if ext and SAFE_EXT.match(ext) else "mp4")
        display = item["name"] if item else (name or f"Film {item_id}")
    else:
        ext_final = ext if ext and SAFE_EXT.match(ext) else "mp4"
        display = (name or f"Avsnitt {item_id}")[:120]

    # Avsluta enhetens egna tidigare sessioner (kanalbyte), först när den nya är beviljad.
    for row in db.q("SELECT id FROM sessions WHERE device_id = ? AND ended IS NULL", (device["id"],)):
        _end(row["id"], "replaced")

    session_id = secrets.token_hex(12)
    db.x(
        """INSERT INTO sessions (id, device_id, type, item_id, item_name, portal_id, started, last_heartbeat)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, device["id"], kind, item_id, display, portal["id"], db.iso(), db.iso()),
    )
    db.log_event("play", {"type": kind, "id": item_id, "name": display, "portal": portal["name"]},
                 device_id=device["id"], session_id=session_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "url": client.stream_url(kind, item_id, ext_final),
        "portal_id": portal["id"],
        "portal_name": portal["name"],
        "name": display,
        "heartbeat_ms": 10000,
        "user_agent": CFG["user_agent"],
    }


def heartbeat(device, session_id, metrics):
    row = db.q1("SELECT * FROM sessions WHERE id = ? AND device_id = ?", (session_id, device["id"]))
    if not row:
        return {"stop": True, "reason": "unknown_session"}
    if row["ended"]:
        return {"stop": row["end_reason"] != "replaced", "reason": row["end_reason"]}
    db.x("UPDATE sessions SET last_heartbeat = ?, metrics = ? WHERE id = ?",
         (db.iso(), json.dumps(metrics or {}), session_id))
    if row["stop_requested"]:
        return {"stop": True, "reason": "takeover",
                "message": "En annan box tog över uppspelningen."}
    return {"stop": False}


def end_session(device, session_id, reason="stopped"):
    row = db.q1("SELECT id FROM sessions WHERE id = ? AND device_id = ?", (session_id, device["id"]))
    if row:
        _end(session_id, str(reason)[:40])
        db.log_event("stop", {"reason": reason}, device_id=device["id"], session_id=session_id)
    return bool(row)


EVENT_KINDS = {"stall", "error", "failover", "speedtest", "start", "quality"}


def report(device, kind, session_id, detail):
    if kind not in EVENT_KINDS:
        raise ValueError("Okänd händelsetyp")
    if not isinstance(detail, dict):
        detail = {}
    detail = {k: v for k, v in list(detail.items())[:30]}
    db.log_event(kind, detail, device_id=device["id"], session_id=session_id)


def cleanup():
    """Stänger sessioner utan heartbeat och rensar gammal historik."""
    db.x("UPDATE sessions SET ended = last_heartbeat, end_reason = 'timeout' "
         "WHERE ended IS NULL AND last_heartbeat < ?", (db.ago(minutes=5),))
