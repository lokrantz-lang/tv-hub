"""Portaler, hälsokontroller och val av aktiv portal."""
import json
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from . import db
from .config import CFG
from .texts import explain
from .xtream import XtreamClient, XtreamError, normalize_base, tcp_probe

NAME_RE = re.compile(r"^[\w .\-()åäöÅÄÖ]{1,60}$")
_pool = ThreadPoolExecutor(max_workers=6)


# ---------- CRUD ----------
def validate(data, partial=False):
    clean = {}
    if "name" in data or not partial:
        name = str(data.get("name", "")).strip()
        if not NAME_RE.match(name):
            return None, "Namnet måste vara 1–60 tecken (bokstäver, siffror, mellanslag, - . ( ))."
        clean["name"] = name
    if "url" in data or not partial:
        url = str(data.get("url", "")).strip()
        if not re.match(r"^https?://[^\s/]+", url):
            return None, "URL:en måste börja med http:// eller https:// följt av adress och port."
        clean["url"] = normalize_base(url)
    if "username" in data or not partial:
        clean["username"] = str(data.get("username", "")).strip()
        if not clean["username"]:
            return None, "Användarnamn saknas."
    # Tomt lösenord vid ändring betyder: behåll det gamla.
    password = data.get("password")
    if password:
        clean["password"] = str(password)
    elif not partial:
        return None, "Lösenord saknas."
    if "priority" in data:
        try:
            clean["priority"] = max(1, min(int(data["priority"]), 999))
        except (TypeError, ValueError):
            return None, "Prioritet måste vara ett heltal 1–999."
    if "enabled" in data:
        clean["enabled"] = 1 if data["enabled"] else 0
    return clean, None


def get(portal_id):
    return db.q1("SELECT * FROM portals WHERE id = ?", (portal_id,))


def all_rows(enabled_only=False):
    sql = "SELECT * FROM portals"
    if enabled_only:
        sql += " WHERE enabled = 1"
    return db.q(sql + " ORDER BY priority, name")


def client(portal, timeout=None):
    return XtreamClient(portal["url"], portal["username"], portal["password"], timeout)


# ---------- kontroller ----------
def check(portal):
    """En hälsokontroll: TCP och inloggning mot API:t. Startar aldrig en ström."""
    c = client(portal)
    record = {
        "tcp_ok": 0, "connect_ms": None, "api_ok": 0, "api_ms": None,
        "error_kind": None, "error": None, "account_status": None,
        "exp_date": None, "max_connections": None, "active_cons": None, "formats": None,
    }
    ok, connect_ms, err = tcp_probe(c.host, c.port, CFG["timeout"])
    record["tcp_ok"], record["connect_ms"] = int(ok), connect_ms
    if not ok:
        record["error_kind"] = err.split(":")[0]
        record["error"] = err
    else:
        try:
            info, ms = c.login()
            timezone = (info.get("server") or {}).get("timezone")
            if timezone:
                db.set_setting(f"portal_tz:{portal['id']}", timezone)
            record.update(
                api_ok=1, api_ms=ms, account_status=info["status"],
                exp_date=info["exp_date"], max_connections=info["max_connections"],
                active_cons=info["active_cons"], formats=",".join(info["formats"]),
            )
        except XtreamError as exc:
            record.update(api_ms=exc.ms, error_kind=exc.kind, error=str(exc))
    db.x(
        """INSERT INTO checks (ts, portal_id, tcp_ok, connect_ms, api_ok, api_ms, error_kind,
           error, account_status, exp_date, max_connections, active_cons, formats)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (db.iso(), portal["id"], record["tcp_ok"], record["connect_ms"], record["api_ok"],
         record["api_ms"], record["error_kind"], record["error"], record["account_status"],
         record["exp_date"], record["max_connections"], record["active_cons"], record["formats"]),
    )
    return record


def check_reference():
    """Kontrollerar internet via referensvärdar, oberoende av leverantören."""
    results = []
    for item in CFG["reference_hosts"].split(","):
        item = item.strip()
        if not item:
            continue
        host, _, port = item.rpartition(":")
        ok, ms, err = tcp_probe(host, int(port or 443), 5)
        results.append({"host": item, "ok": ok, "ms": ms, "error": err})
    any_ok = any(r["ok"] for r in results) if results else True
    best_ms = min((r["ms"] for r in results if r["ms"] is not None), default=None)
    db.x(
        "INSERT INTO net_checks (ts, ok, ms, detail) VALUES (?, ?, ?, ?)",
        (db.iso(), int(any_ok), best_ms, json.dumps(results)),
    )
    return any_ok


def monitor_loop(stop_event):
    last_portal = last_net = -10**9
    while not stop_event.is_set():
        current = time.monotonic()
        if current - last_portal >= CFG["check_interval"]:
            last_portal = current
            for portal in all_rows(enabled_only=True):
                _pool.submit(_safe, check, portal)
        if current - last_net >= CFG["net_check_interval"]:
            last_net = current
            _pool.submit(_safe, check_reference)
        stop_event.wait(2)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:  # loggas, får aldrig stoppa loopen
        print(f"[fel] {fn.__name__}: {exc}", flush=True)
        return None


# ---------- sammanfattning ----------
def summary(portal):
    pid = portal["id"]
    last = db.q1("SELECT * FROM checks WHERE portal_id = ? ORDER BY id DESC LIMIT 1", (pid,))
    hour = db.q1(
        """SELECT COUNT(*) AS total, SUM(CASE WHEN api_ok = 0 THEN 1 ELSE 0 END) AS failed
           FROM checks WHERE portal_id = ? AND ts >= ?""",
        (pid, db.ago(hours=1)),
    )
    recent = db.q("SELECT api_ok FROM checks WHERE portal_id = ? ORDER BY id DESC LIMIT 20", (pid,))
    streak = 0
    for row in recent:
        if row["api_ok"]:
            break
        streak += 1
    day = [r["api_ms"] for r in db.q(
        "SELECT api_ms FROM checks WHERE portal_id = ? AND api_ok = 1 AND ts >= ?",
        (pid, db.ago(hours=24)),
    ) if r["api_ms"] is not None]
    recent_ms = [r["api_ms"] for r in db.q(
        "SELECT api_ms FROM checks WHERE portal_id = ? AND api_ok = 1 AND ts >= ?",
        (pid, db.ago(minutes=15)),
    ) if r["api_ms"] is not None]
    return {
        "id": pid,
        "name": portal["name"],
        "url": portal["url"],
        "username": portal["username"],
        "has_password": bool(portal["password"]),
        "priority": portal["priority"],
        "enabled": bool(portal["enabled"]),
        "ok": bool(last["api_ok"]) if last else None,
        "last_check": dict(last, error_text=explain(last["error_kind"]) if last["error_kind"] else None) if last else None,
        "failed_last_hour": hour["failed"] or 0,
        "checks_last_hour": hour["total"] or 0,
        "consecutive_failures": streak,
        "api_ms_median_24h": round(statistics.median(day)) if day else None,
        "api_ms_median_15m": round(statistics.median(recent_ms)) if recent_ms else None,
    }


def summaries():
    items = [summary(p) for p in all_rows()]
    best = pick_best(items)
    for item in items:
        item["active"] = item["id"] == best
    return items, best


def pick_best(items, exclude=()):
    """Aktiv portal: fungerar nu, sedan prioritet, sedan färst fel, sedan snabbast."""
    candidates = [
        s for s in items
        if s["enabled"] and s["ok"] and s["id"] not in exclude
    ]
    if not candidates:
        # Inga lyckade kontroller ännu: ta den med högst prioritet som inte är utesluten.
        fallback = [s for s in items if s["enabled"] and s["ok"] is None and s["id"] not in exclude]
        return fallback[0]["id"] if fallback else None
    candidates.sort(key=lambda s: (
        s["priority"],
        s["failed_last_hour"],
        s["api_ms_median_15m"] if s["api_ms_median_15m"] is not None else 10**9,
    ))
    return candidates[0]["id"]


def best_portal(exclude=()):
    items = [summary(p) for p in all_rows(enabled_only=True)]
    pid = pick_best(items, exclude)
    return get(pid) if pid else None


def output_format(portal):
    if CFG["output_format"] != "auto":
        return CFG["output_format"]
    last = db.q1(
        "SELECT formats FROM checks WHERE portal_id = ? AND api_ok = 1 ORDER BY id DESC LIMIT 1",
        (portal["id"],),
    )
    formats = (last["formats"] or "").split(",") if last else []
    if "ts" in formats or not formats:
        return "ts"
    return "m3u8" if "m3u8" in formats else formats[0]


def run_check_now(portal_id):
    portal = get(portal_id)
    return check(portal) if portal else None

