"""Fortsätt titta, sedda avsnitt, senaste kanaler, sparade filter och bevakningar."""
import json
import os
import threading
import urllib.request

from . import catalog, db
from .catalog import Scope

FINISHED_RATIO = 0.92
FINISHED_LEFT_MS = 3 * 60 * 1000
MIN_RESUME_MS = 60 * 1000


# ====================================================================
# Fortsätt titta
# ====================================================================
def update_progress(session, position_ms, duration_ms):
    """Anropas vid heartbeat och stopp för filmer och avsnitt."""
    if session["type"] not in ("vod", "episode"):
        return
    try:
        position_ms, duration_ms = int(position_ms or 0), int(duration_ms or 0)
    except (TypeError, ValueError):
        return
    if position_ms <= 0:
        return
    adult = session["adult"]
    key = (adult, session["type"], session["item_id"])
    finished = duration_ms > 0 and (position_ms >= duration_ms * FINISHED_RATIO
                                    or duration_ms - position_ms <= FINISHED_LEFT_MS)
    with db.tx() as conn:
        if finished:
            conn.execute("DELETE FROM progress WHERE adult = ? AND type = ? AND item_id = ?", key)
            conn.execute(
                "INSERT OR REPLACE INTO watched (adult, type, item_id, series_id, at) VALUES (?,?,?,?,?)",
                (*key, session["series_id"], db.iso()),
            )
            return
        if session["type"] == "episode" and session["series_id"]:
            # Bara senaste avsnittet per serie i "Fortsätt titta".
            conn.execute(
                "DELETE FROM progress WHERE adult = ? AND type = 'episode' AND series_id = ? AND item_id != ?",
                (adult, session["series_id"], session["item_id"]),
            )
        conn.execute(
            """INSERT OR REPLACE INTO progress (adult, type, item_id, series_id, name, icon, ext,
               position_ms, duration_ms, updated) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (*key, session["series_id"], session["item_name"], session["icon"], session["ext"],
             position_ms, duration_ms, db.iso()),
        )


def resume_position(adult, kind, item_id):
    row = db.q1("SELECT position_ms, duration_ms FROM progress WHERE adult = ? AND type = ? AND item_id = ?",
                (adult, kind, str(item_id)))
    if row and row["position_ms"] >= MIN_RESUME_MS:
        return {"position_ms": row["position_ms"], "duration_ms": row["duration_ms"]}
    return None


def continue_watching(scope, limit=20):
    adult = scope.adult or 0
    out = []
    for row in db.q(
        "SELECT * FROM progress WHERE adult = ? AND position_ms >= ? ORDER BY updated DESC LIMIT ?",
        (adult, MIN_RESUME_MS, limit * 2),
    ):
        # Kontrollera mot boxens scope via filmen eller serien.
        ref_type, ref_id = ("vod", row["item_id"]) if row["type"] == "vod" else ("series", row["series_id"])
        if not ref_id or not catalog.in_scope(ref_type, ref_id, scope):
            continue
        parent = catalog.get_item(ref_type, ref_id)
        out.append({
            "type": row["type"], "id": row["item_id"], "series_id": row["series_id"],
            "name": row["name"], "icon": row["icon"] or (parent or {}).get("icon"), "ext": row["ext"],
            "position_ms": row["position_ms"], "duration_ms": row["duration_ms"],
            "series_name": parent["name"] if parent and row["type"] == "episode" else None,
        })
        if len(out) >= limit:
            break
    return out


def series_progress(adult, series_id):
    progress = {r["item_id"]: {"position_ms": r["position_ms"], "duration_ms": r["duration_ms"]}
                for r in db.q("SELECT * FROM progress WHERE adult = ? AND type = 'episode' AND series_id = ?",
                              (adult, str(series_id)))}
    watched = [r["item_id"] for r in db.q(
        "SELECT item_id FROM watched WHERE adult = ? AND type = 'episode' AND series_id = ?", (adult, str(series_id)))]
    return progress, watched


def is_watched(adult, kind, item_id):
    return db.q1("SELECT 1 FROM watched WHERE adult = ? AND type = ? AND item_id = ?",
                 (adult, kind, str(item_id))) is not None


# ====================================================================
# Senaste kanaler
# ====================================================================
def recent_channels(device_id, scope, limit=20):
    rows = db.q(
        """SELECT item_id, MAX(started) AS last FROM sessions
           WHERE device_id = ? AND type = 'live' AND adult = ?
           GROUP BY item_id ORDER BY last DESC LIMIT ?""",
        (device_id, scope.adult or 0, limit * 2),
    )
    out = []
    for row in rows:
        if not catalog.in_scope("live", row["item_id"], scope):
            continue
        item = catalog.get_item("live", row["item_id"])
        if item:
            out.append(item)
        if len(out) >= limit:
            break
    return out


# ====================================================================
# Sparade filter
# ====================================================================
FILTER_KEYS = ("sort", "min_rating", "min_year", "max_year", "min_duration", "max_duration", "genre", "category_id")


def clean_params(params):
    return {k: params[k] for k in FILTER_KEYS if k in params and params[k] not in (None, "")}


def saved_filters(adult=None):
    sql, args = "SELECT * FROM saved_filters", []
    if adult is not None:
        sql += " WHERE adult = ?"
        args.append(adult)
    out = []
    for row in db.q(sql + " ORDER BY id", args):
        item = dict(row)
        item["params"] = json.loads(item["params"])
        out.append(item)
    return out


def create_filter(name, kind, params, adult=0):
    name = (name or "").strip()[:40]
    if not name:
        raise ValueError("Namn saknas")
    if kind not in catalog.TYPES:
        raise ValueError("Okänd typ")
    return db.x(
        "INSERT INTO saved_filters (name, type, adult, params, created) VALUES (?,?,?,?,?)",
        (name, kind, int(bool(adult)), json.dumps(clean_params(params)), db.iso()),
    ).lastrowid


def delete_filter(filter_id, adult=None):
    sql, args = "DELETE FROM saved_filters WHERE id = ?", [filter_id]
    if adult is not None:
        sql += " AND adult = ?"
        args.append(adult)
    return db.x(sql, args).rowcount > 0


# ====================================================================
# Bevakningar
# ====================================================================
WATCH_SCOPE = Scope(adult=0)


def _matches(watch):
    return catalog.search(watch["query"], watch["type"], limit=200, scope=WATCH_SCOPE)


def watches():
    out = []
    for row in db.q("SELECT * FROM watches ORDER BY id DESC"):
        item = dict(row)
        item.pop("seen", None)
        item["hits"] = db.rows(db.q(
            "SELECT type, item_id, name, at FROM watch_hits WHERE watch_id = ? ORDER BY id DESC LIMIT 20", (row["id"],)))
        out.append(item)
    return out


def create_watch(query, kind=None):
    query = (query or "").strip()[:80]
    if len(query) < 2:
        raise ValueError("Skriv minst två tecken.")
    if kind not in catalog.TYPES:
        kind = None
    watch = {"query": query, "type": kind}
    # Det som redan finns räknas som sett. Bara nya titlar ger notis.
    seen = sorted(f"{i['type']}:{i['id']}" for i in _matches(watch))
    watch_id = db.x("INSERT INTO watches (query, type, created, seen) VALUES (?,?,?,?)",
                    (query, kind, db.iso(), json.dumps(seen))).lastrowid
    return {"id": watch_id, "existing": len(seen)}


def delete_watch(watch_id):
    db.x("DELETE FROM watch_hits WHERE watch_id = ?", (watch_id,))
    return db.x("DELETE FROM watches WHERE id = ?", (watch_id,)).rowcount > 0


def check_watches():
    """Körs efter varje synk. Nya träffar loggas och skickas till Home Assistant."""
    new_hits = []
    for row in db.q("SELECT * FROM watches"):
        watch = dict(row)
        seen = set(json.loads(watch["seen"] or "[]"))
        current = _matches(watch)
        fresh = [i for i in current if f"{i['type']}:{i['id']}" not in seen]
        for item in fresh:
            db.x("INSERT INTO watch_hits (watch_id, type, item_id, name, at) VALUES (?,?,?,?,?)",
                 (watch["id"], item["type"], item["id"], item["name"], db.iso()))
            new_hits.append((watch["query"], item))
        if fresh:
            seen |= {f"{i['type']}:{i['id']}" for i in fresh}
            db.x("UPDATE watches SET seen = ? WHERE id = ?", (json.dumps(sorted(seen)), watch["id"]))
    if new_hits:
        db.log_event("watch_hit", {"titles": [i["name"] for _, i in new_hits][:20]})
        threading.Thread(target=notify_home_assistant, args=(new_hits,), daemon=True).start()
    return new_hits


def recent_hits(scope, days=14, limit=40):
    out = []
    for row in db.q("SELECT DISTINCT type, item_id FROM watch_hits WHERE at >= ? ORDER BY id DESC LIMIT ?",
                    (db.ago(days=days), limit * 2)):
        if catalog.in_scope(row["type"], row["item_id"], scope):
            item = catalog.get_item(row["type"], row["item_id"])
            if item:
                out.append(item)
        if len(out) >= limit:
            break
    return out


def notify_home_assistant(hits):
    """Skapar en notis i Home Assistant och skickar händelsen tv_hub_watch_hit (för egna automationer)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return
    titles = [item["name"] for _, item in hits]
    message = "Nytt hos Toucan: " + ", ".join(titles[:10]) + (" …" if len(titles) > 10 else "")
    calls = [
        ("services/persistent_notification/create",
         {"title": "TV Hub: bevakning", "message": message, "notification_id": "tv_hub_watch"}),
        ("events/tv_hub_watch_hit", {"titles": titles, "message": message}),
    ]
    for path, body in calls:
        try:
            request = urllib.request.Request(
                f"http://supervisor/core/api/{path}", data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            urllib.request.urlopen(request, timeout=10).read()
        except Exception as exc:
            print(f"[fel] notis till Home Assistant: {exc}", flush=True)


catalog._after_sync.append(check_watches)
