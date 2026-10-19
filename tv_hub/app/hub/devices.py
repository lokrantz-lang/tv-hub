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

from datetime import datetime, timezone

from . import catalog, db, extras, portals, recordings
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
    for row in db.q("""SELECT id, name, model, app_version, created, last_seen, last_ip, allow_adult, only_group_id
                       FROM devices ORDER BY id"""):
        item = dict(row)
        item["allow_adult"] = bool(item["allow_adult"])
        item["playing"] = active_session_for(row["id"])
        out.append(item)
    return out


def update_device(device_id, data):
    if not db.q1("SELECT 1 FROM devices WHERE id = ?", (device_id,)):
        raise LookupError("Boxen finns inte")
    if "name" in data:
        rename(device_id, data["name"])
    if "allow_adult" in data:
        db.x("UPDATE devices SET allow_adult = ? WHERE id = ?", (1 if data["allow_adult"] else 0, device_id))
    if "only_group_id" in data:
        group_id = data["only_group_id"]
        if group_id in (None, "", 0, "0"):
            group_id = None
        else:
            group_id = int(group_id)
            if not db.q1("SELECT 1 FROM groups WHERE id = ?", (group_id,)):
                raise LookupError("Gruppen finns inte")
        db.x("UPDATE devices SET only_group_id = ? WHERE id = ?", (group_id, device_id))


def scope_for(device, wants_adult, wants_history=True):
    """Vad boxen får se: läget från appen, begränsat av boxens profil.
    wants_history gäller bara vuxenläget (dolt val i appen: höger tre gånger på Inställningar)."""
    adult = 1 if wants_adult and device["allow_adult"] else 0
    return catalog.Scope(adult=adult, group_id=device["only_group_id"], hide_hidden=True,
                         history=bool(wants_history) or not adult)


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
RECORD_TYPES = ("record", "record_archive")


def _active_rows(exclude_device=None, watching_only=False):
    sql = """SELECT s.*, d.name AS device_name FROM sessions s JOIN devices d ON d.id = s.device_id
             WHERE s.ended IS NULL AND s.last_heartbeat >= ?"""
    args = [db.ago(seconds=ACTIVE_WINDOW_S)]
    if watching_only:
        sql += " AND s.type NOT IN ('record', 'record_archive')"
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
    rows = [r for r in _active_rows(watching_only=True) if r["device_id"] == device_id]
    if not rows:
        return None
    r = rows[0]
    return {"session_id": r["id"], "type": r["type"], "item_id": r["item_id"],
            "item_name": r["item_name"], "started": r["started"]}


def _end(session_id, reason):
    db.x("UPDATE sessions SET ended = ?, end_reason = ? WHERE id = ? AND ended IS NULL",
         (db.iso(), reason, session_id))


def _timeshift_start(portal, start_ts):
    """Catch-up-starttid i källans tidszon, format ÅÅÅÅ-MM-DD:TT-MM."""
    tz_name = db.get_setting(f"portal_tz:{portal['id']}")
    zone = timezone.utc
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(tz_name)
        except Exception:
            zone = timezone.utc
    return datetime.fromtimestamp(int(start_ts), zone).strftime("%Y-%m-%d:%H-%M")


def request_play(device, kind, item_id, ext=None, force=False, exclude_portals=(), name=None,
                 scope=None, series_id=None, start=None, duration=None, icon=None, recording_id=None):
    """recording_id: strömmen är en inspelning. Den räknas inte av anslutningsvakten
    (man kan titta på en kanal och spela in en annan) och avslutar inte boxens tittande.

    Returnerar ett av:
    {"status": "conflict", ...}  en annan box tittar, fråga användaren
    {"status": "waiting", ...}   övertagande pågår, fråga igen om en sekund
    {"status": "ok", "url": ..., "session_id": ...}
    {"status": "error", "error": ...}
    """
    if kind not in ("live", "vod", "episode", "catchup"):
        return {"status": "error", "error": "Okänd typ"}
    item_id = str(item_id)
    if not SAFE_ID.match(item_id):
        return {"status": "error", "error": "Ogiltigt id"}
    series_id = str(series_id) if series_id not in (None, "") else None
    if series_id is not None and not SAFE_ID.match(series_id):
        return {"status": "error", "error": "Ogiltigt serie-id"}

    recording = None
    if recording_id is not None:
        recording = recordings.lookup_for_play(device, recording_id)
        if recording is None or str(recording["stream_id"]) != item_id or kind not in ("live", "catchup"):
            return {"status": "error", "error": "Inspelningen finns inte längre."}

    # Boxens läge och profil. En inspelning prövades mot läget när den planerades.
    if scope is not None and recording is None:
        if kind in ("live", "vod", "catchup"):
            ref = ("live" if kind == "catchup" else kind, item_id)
        else:
            ref = ("series", series_id) if series_id else None
        if ref is None:
            if scope.group_id is not None or scope.adult:
                return {"status": "error", "error": "Finns inte."}
        elif not catalog.in_scope(ref[0], ref[1], scope) and \
                (catalog.get_item(*ref) is not None or scope.group_id is not None):
            return {"status": "error", "error": "Finns inte."}

    if kind == "catchup":
        try:
            start, duration = int(start), int(duration)
        except (TypeError, ValueError):
            return {"status": "error", "error": "Starttid och längd krävs"}
        if not 0 < duration <= 600 or start > time.time():
            return {"status": "error", "error": "Ogiltig tidpunkt"}

    if (db.get_setting("server_test_until", 0) or 0) > time.time():
        return {"status": "waiting", "retry_ms": 2000,
                "message": "Servern kör ett kapacitetstest. Uppspelningen startar strax."}

    others = [] if recording else _active_rows(exclude_device=device["id"], watching_only=True)
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
        for o in _active_rows(exclude_device=device["id"], watching_only=True):
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
    item = None
    if kind in ("live", "catchup"):
        item = catalog.get_item("live", item_id)
        ext_final = portals.output_format(portal)
        display = item["name"] if item else (name or f"Kanal {item_id}")
        if kind == "catchup":
            display = (name or display)[:120]
            url = client.timeshift_url(item_id, _timeshift_start(portal, start), duration, "ts")
        else:
            url = client.stream_url("live", item_id, ext_final)
    elif kind == "vod":
        item = catalog.get_item("vod", item_id)
        ext_final = (item and item["ext"]) or (ext if ext and SAFE_EXT.match(ext) else "mp4")
        display = item["name"] if item else (name or f"Film {item_id}")
        url = client.stream_url("vod", item_id, ext_final)
    else:
        ext_final = ext if ext and SAFE_EXT.match(ext) else "mp4"
        display = (name or f"Avsnitt {item_id}")[:120]
        url = client.stream_url("episode", item_id, ext_final)
    if icon is None and item:
        icon = item.get("icon")
    if icon is None and series_id:
        parent = catalog.get_item("series", series_id)
        icon = parent["icon"] if parent else None

    # Avsluta enhetens egna tidigare sessioner (kanalbyte), först när den nya är beviljad.
    # Inspelningar påverkar inte tittandet och tvärtom.
    if recording is None:
        for row in db.q("SELECT id FROM sessions WHERE device_id = ? AND ended IS NULL "
                        "AND type NOT IN ('record', 'record_archive')", (device["id"],)):
            _end(row["id"], "replaced")
        adult = 1 if scope is not None and scope.adult else 0
    else:
        adult = recording["adult"]
        display = f"Inspelning: {recording['title']}"[:120]
    session_id = secrets.token_hex(12)
    session_type = kind if recording is None else ("record" if kind == "live" else "record_archive")
    db.x(
        """INSERT INTO sessions (id, device_id, type, item_id, item_name, portal_id, started, last_heartbeat,
           adult, series_id, ext, icon) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, device["id"], session_type, item_id, display, portal["id"], db.iso(), db.iso(),
         adult, series_id, ext_final, str(icon)[:500] if icon else None),
    )
    if recording is not None:
        recordings.attach_session(device, recording["id"], item_id, session_id)
    db.log_event("record" if recording else "play",
                 {"type": kind, "id": item_id, "name": display, "portal": portal["name"]},
                 device_id=device["id"], session_id=session_id)
    return {
        "status": "ok",
        "session_id": session_id,
        "url": url,
        "portal_id": portal["id"],
        "portal_name": portal["name"],
        "name": display,
        "heartbeat_ms": 10000,
        "user_agent": CFG["user_agent"],
        "resume": extras.resume_position(adult, kind, item_id)
        if kind in ("vod", "episode") and not (scope is not None and scope.private) else None,
        "archive_days": int((item or {}).get("archive_days") or 0) if kind in ("live", "catchup") else 0,
    }


def heartbeat(device, session_id, metrics):
    row = db.q1("SELECT * FROM sessions WHERE id = ? AND device_id = ?", (session_id, device["id"]))
    if not row:
        return {"stop": True, "reason": "unknown_session"}
    if row["ended"]:
        return {"stop": row["end_reason"] != "replaced", "reason": row["end_reason"]}
    metrics = metrics if isinstance(metrics, dict) else {}
    db.x("UPDATE sessions SET last_heartbeat = ?, metrics = ? WHERE id = ?",
         (db.iso(), json.dumps(metrics), session_id))
    extras.update_progress(row, metrics.get("position_ms"), metrics.get("duration_ms"))
    if row["stop_requested"] and row["type"] in RECORD_TYPES:
        return {"stop": True, "reason": "record_stopped", "message": "Inspelningen stoppades från webbadminen."}
    if row["stop_requested"]:
        return {"stop": True, "reason": "takeover",
                "message": "En annan box tog över uppspelningen."}
    return {"stop": False}


def end_session(device, session_id, reason="stopped", metrics=None):
    row = db.q1("SELECT * FROM sessions WHERE id = ? AND device_id = ?", (session_id, device["id"]))
    if row:
        if isinstance(metrics, dict):
            extras.update_progress(row, metrics.get("position_ms"), metrics.get("duration_ms"))
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
