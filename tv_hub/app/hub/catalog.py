"""Katalog: synk från Xtream, sök, favoriter och egna grupper."""
import json
import re
import threading
import time

from . import db, portals
from .config import CFG
from .xtream import XtreamError

TYPES = ("live", "vod", "series")
STEPS = [
    ("live", "categories", "get_live_categories"),
    ("live", "items", "get_live_streams"),
    ("vod", "categories", "get_vod_categories"),
    ("vod", "items", "get_vod_streams"),
    ("series", "categories", "get_series_categories"),
    ("series", "items", "get_series"),
]
_sync_lock = threading.Lock()
_sync_state = {"running": False, "step": None}


def normalize(text):
    text = (text or "").casefold()
    text = re.sub(r"[|:_\-\[\]().,/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _category(raw):
    cid = raw.get("category_id")
    if cid in (None, "") and raw.get("category_ids"):
        cid = raw["category_ids"][0]
    return None if cid is None else str(cid)


def _map_item(kind, raw):
    if kind == "live":
        item_id, icon = raw.get("stream_id"), raw.get("stream_icon")
    elif kind == "vod":
        item_id, icon = raw.get("stream_id"), raw.get("stream_icon")
    else:
        item_id, icon = raw.get("series_id"), raw.get("cover")
    if item_id in (None, ""):
        return None
    name = str(raw.get("name") or raw.get("title") or "").strip() or f"#{item_id}"
    return (
        kind, str(item_id), name, normalize(name), _category(raw), icon or None,
        raw.get("container_extension") or None,
        str(raw.get("rating") or "") or None,
        _int(raw.get("added") or raw.get("last_modified")),
        _int(raw.get("num")),
        raw.get("epg_channel_id") or None,
    )


# ---------- synk ----------
def sync_status():
    return dict(_sync_state)


def start_sync(trigger="manual"):
    """Startar synk i bakgrunden. Returnerar False om en synk redan körs."""
    if _sync_lock.locked():
        return False
    threading.Thread(target=sync, args=(trigger,), daemon=True).start()
    return True


def sync(trigger="schedule"):
    if not _sync_lock.acquire(blocking=False):
        return None
    try:
        _sync_state["running"] = True
        return _sync(trigger)
    finally:
        _sync_state.update(running=False, step=None)
        _sync_lock.release()


def _sync(trigger):
    run_id = db.x(
        "INSERT INTO sync_runs (started, status, trigger) VALUES (?, 'running', ?)",
        (db.iso(), trigger),
    ).lastrowid
    steps = []
    tried = []
    portal = portals.best_portal()
    if portal is None:
        detail = {"steps": [], "error": "Ingen aktiv portal att synka från."}
        _finish(run_id, None, "failed", detail)
        return detail

    for kind, part, action in STEPS:
        _sync_state["step"] = f"{kind}/{part}"
        result = {"type": kind, "part": part, "action": action}
        attempt_portal = portal
        while True:
            client = portals.client(attempt_portal, timeout=CFG["sync_timeout"])
            start = time.perf_counter()
            try:
                data, ms = client.list_action(action, timeout=CFG["sync_timeout"])
                result.update(ok=True, ms=ms, count=len(data), portal=attempt_portal["name"])
                _store(kind, part, data, result)
                break
            except XtreamError as exc:
                result.update(
                    ok=False, ms=exc.ms or round((time.perf_counter() - start) * 1000),
                    error_kind=exc.kind, error=str(exc), portal=attempt_portal["name"],
                )
                tried.append(attempt_portal["id"])
                # Nätverksfel: prova nästa portal för samma steg.
                if exc.kind in ("dns", "refused", "timeout", "network", "reset") or exc.kind.startswith("http_5"):
                    fallback = portals.best_portal(exclude=tried)
                    if fallback is not None:
                        result.setdefault("failovers", []).append(
                            {"from": attempt_portal["name"], "to": fallback["name"], "reason": exc.kind}
                        )
                        attempt_portal = fallback
                        continue
                break
        steps.append(result)

    failed = [s for s in steps if not s.get("ok") or s.get("kept_old")]
    status = "ok" if not failed else ("partial" if len(failed) < len(steps) else "failed")
    detail = {"steps": steps}
    _finish(run_id, portal["id"], status, detail)
    db.log_event("sync", {"status": status, "failed_steps": [f"{s['type']}/{s['part']}" for s in failed]})
    return detail


def _finish(run_id, portal_id, status, detail):
    db.x(
        "UPDATE sync_runs SET finished = ?, portal_id = ?, status = ?, detail = ? WHERE id = ?",
        (db.iso(), portal_id, status, json.dumps(detail, ensure_ascii=False), run_id),
    )


def _store(kind, part, data, result):
    if part == "categories":
        rows = []
        for index, raw in enumerate(data):
            cid = raw.get("category_id")
            if cid in (None, ""):
                continue
            rows.append((kind, str(cid), str(raw.get("category_name") or cid), index))
        existing = db.q1("SELECT COUNT(*) AS n FROM categories WHERE type = ?", (kind,))["n"]
        if not rows and existing:
            result.update(kept_old=True, error_kind="empty_list",
                          error="Källan returnerade en tom lista. Den gamla listan behölls.")
            return
        with db.tx() as conn:
            conn.execute("DELETE FROM categories WHERE type = ?", (kind,))
            conn.executemany(
                "INSERT OR REPLACE INTO categories (type, category_id, name, sort) VALUES (?,?,?,?)", rows
            )
        return

    rows = [r for r in (_map_item(kind, raw) for raw in data if isinstance(raw, dict)) if r]
    existing = db.q1("SELECT COUNT(*) AS n FROM items WHERE type = ?", (kind,))["n"]
    if not rows and existing:
        result.update(kept_old=True, error_kind="empty_list",
                      error="Källan returnerade en tom lista. Den gamla listan behölls.")
        return
    # Skydd: om listan krympt kraftigt är det troligen ett fel hos källan.
    if existing >= 50 and len(rows) < existing * 0.5:
        result.update(
            kept_old=True, error_kind="shrunk",
            error=f"Källan gav {len(rows)} objekt mot tidigare {existing}. Den gamla listan behölls.",
        )
        return
    with db.tx() as conn:
        conn.execute("DELETE FROM items WHERE type = ?", (kind,))
        conn.executemany(
            """INSERT OR REPLACE INTO items (type, item_id, name, name_norm, category_id, icon, ext,
               rating, added, num, epg_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    result["stored"] = len(rows)


def sync_loop(stop_event):
    # Första synken direkt om katalogen är tom, annars enligt schema.
    stop_event.wait(20)
    while not stop_event.is_set():
        last = db.q1("SELECT started FROM sync_runs WHERE status IN ('ok','partial') ORDER BY id DESC LIMIT 1")
        due = True
        if last:
            age_h = (db.now() - db.parse_iso(last["started"])).total_seconds() / 3600
            due = age_h >= CFG["sync_interval_hours"]
        if due and portals.best_portal() is not None:
            sync("schedule")
        stop_event.wait(300)


def sync_runs(limit=20):
    out = []
    for row in db.q("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)):
        item = dict(row)
        item["detail"] = json.loads(item["detail"]) if item["detail"] else None
        out.append(item)
    return out


def counts():
    result = {}
    for kind in TYPES:
        result[kind] = {
            "items": db.q1("SELECT COUNT(*) AS n FROM items WHERE type = ?", (kind,))["n"],
            "categories": db.q1("SELECT COUNT(*) AS n FROM categories WHERE type = ?", (kind,))["n"],
        }
    return result


# ---------- läsning ----------
ITEM_COLS = "i.type, i.item_id, i.name, i.category_id, i.icon, i.ext, i.rating, i.added, i.num, i.epg_id"


def _item(row, favs=None):
    item = {
        "type": row["type"], "id": row["item_id"], "name": row["name"],
        "category_id": row["category_id"], "icon": row["icon"], "ext": row["ext"],
        "rating": row["rating"], "added": row["added"], "num": row["num"], "epg_id": row["epg_id"],
    }
    if favs is not None:
        item["favorite"] = (row["type"], row["item_id"]) in favs
    return item


def favorite_keys():
    return {(r["type"], r["item_id"]) for r in db.q("SELECT type, item_id FROM favorites")}


def categories(kind):
    rows = db.q(
        """SELECT c.category_id, c.name, c.sort,
                  (SELECT COUNT(*) FROM items i WHERE i.type = c.type AND i.category_id = c.category_id) AS count
           FROM categories c WHERE c.type = ? ORDER BY c.sort""",
        (kind,),
    )
    return db.rows(rows)


def items_in_category(kind, category_id, offset=0, limit=200):
    order = "i.num, i.name" if kind == "live" else "i.added DESC, i.name"
    rows = db.q(
        f"SELECT {ITEM_COLS} FROM items i WHERE i.type = ? AND i.category_id = ? ORDER BY {order} LIMIT ? OFFSET ?",
        (kind, str(category_id), limit, offset),
    )
    favs = favorite_keys()
    return [_item(r, favs) for r in rows]


def recent(kind, limit=50):
    rows = db.q(
        f"SELECT {ITEM_COLS} FROM items i WHERE i.type = ? ORDER BY i.added DESC LIMIT ?",
        (kind, limit),
    )
    favs = favorite_keys()
    return [_item(r, favs) for r in rows]


def search(query, kind=None, limit=100, offset=0):
    tokens = normalize(query).split()
    if not tokens:
        return []
    where = " AND ".join("i.name_norm LIKE ?" for _ in tokens)
    args = [f"%{t}%" for t in tokens]
    if kind in TYPES:
        where += " AND i.type = ?"
        args.append(kind)
    # Exakta träffar och träffar i början av namnet först.
    first = tokens[0]
    rows = db.q(
        f"""SELECT {ITEM_COLS}, c.name AS category_name FROM items i
            LEFT JOIN categories c ON c.type = i.type AND c.category_id = i.category_id
            WHERE {where}
            ORDER BY CASE WHEN i.name_norm = ? THEN 0 WHEN i.name_norm LIKE ? THEN 1 ELSE 2 END,
                     CASE i.type WHEN 'live' THEN 0 WHEN 'vod' THEN 1 ELSE 2 END,
                     i.name
            LIMIT ? OFFSET ?""",
        (*args, " ".join(tokens), f"{first}%", limit, offset),
    )
    favs = favorite_keys()
    out = []
    for row in rows:
        item = _item(row, favs)
        item["category_name"] = row["category_name"]
        out.append(item)
    return out


def get_item(kind, item_id):
    row = db.q1(f"SELECT {ITEM_COLS} FROM items i WHERE i.type = ? AND i.item_id = ?", (kind, str(item_id)))
    return _item(row) if row else None


def neighbours(kind, item_id):
    """Föregående och nästa kanal i samma kategori (för kanal upp/ner)."""
    item = get_item(kind, item_id)
    if not item or kind != "live":
        return None, None
    rows = db.q(
        "SELECT item_id FROM items WHERE type = 'live' AND category_id IS ? ORDER BY num, name",
        (item["category_id"],),
    )
    ids = [r["item_id"] for r in rows]
    if item_id not in ids:
        return None, None
    index = ids.index(item_id)
    prev_id = ids[index - 1] if index > 0 else ids[-1]
    next_id = ids[(index + 1) % len(ids)]
    return prev_id, next_id


# ---------- favoriter ----------
def _resolved(rows):
    out = []
    for row in rows:
        if row["name"] is None:
            out.append({"type": row["ref_type"], "id": row["ref_id"], "name": None, "missing": True})
        else:
            item = _item(row)
            item["favorite"] = True if "is_fav" not in row.keys() else bool(row["is_fav"])
            item["missing"] = False
            out.append(item)
    return out


def favorites():
    rows = db.q(
        f"""SELECT f.type AS ref_type, f.item_id AS ref_id, 1 AS is_fav, {ITEM_COLS}
            FROM favorites f LEFT JOIN items i ON i.type = f.type AND i.item_id = f.item_id
            ORDER BY CASE f.type WHEN 'live' THEN 0 WHEN 'vod' THEN 1 ELSE 2 END, f.added"""
    )
    return _resolved(rows)


def set_favorite(kind, item_id, on):
    if kind not in TYPES:
        raise ValueError("Okänd typ")
    if on:
        db.x("INSERT OR IGNORE INTO favorites (type, item_id, added) VALUES (?, ?, ?)",
             (kind, str(item_id), db.iso()))
    else:
        db.x("DELETE FROM favorites WHERE type = ? AND item_id = ?", (kind, str(item_id)))
    return on


# ---------- grupper ----------
def groups(with_items=False):
    out = []
    favs = favorite_keys()
    for g in db.q("SELECT * FROM groups ORDER BY sort, name"):
        group = {"id": g["id"], "name": g["name"], "sort": g["sort"]}
        group["count"] = db.q1("SELECT COUNT(*) AS n FROM group_items WHERE group_id = ?", (g["id"],))["n"]
        if with_items:
            group["items"] = group_items(g["id"], favs)
        out.append(group)
    return out


def group_items(group_id, favs=None):
    favs = favs if favs is not None else favorite_keys()
    rows = db.q(
        f"""SELECT gi.type AS ref_type, gi.item_id AS ref_id, {ITEM_COLS}
            FROM group_items gi LEFT JOIN items i ON i.type = gi.type AND i.item_id = gi.item_id
            WHERE gi.group_id = ? ORDER BY gi.sort, i.name""",
        (group_id,),
    )
    out = _resolved(rows)
    for item in out:
        item["favorite"] = (item["type"], item["id"]) in favs
    return out


def create_group(name, sort=100):
    name = (name or "").strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("Gruppnamnet måste vara 1–60 tecken.")
    return db.x("INSERT INTO groups (name, sort, created) VALUES (?, ?, ?)", (name, int(sort), db.iso())).lastrowid


def update_group(group_id, name=None, sort=None):
    if name is not None:
        name = name.strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("Gruppnamnet måste vara 1–60 tecken.")
        db.x("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))
    if sort is not None:
        db.x("UPDATE groups SET sort = ? WHERE id = ?", (int(sort), group_id))


def delete_group(group_id):
    with db.tx() as conn:
        conn.execute("DELETE FROM group_items WHERE group_id = ?", (group_id,))
        cur = conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    return cur.rowcount > 0


def add_to_group(group_id, kind, item_id):
    if kind not in TYPES:
        raise ValueError("Okänd typ")
    if not db.q1("SELECT 1 FROM groups WHERE id = ?", (group_id,)):
        raise LookupError("Gruppen finns inte")
    top = db.q1("SELECT COALESCE(MAX(sort), 0) AS m FROM group_items WHERE group_id = ?", (group_id,))["m"]
    db.x("INSERT OR IGNORE INTO group_items (group_id, type, item_id, sort) VALUES (?, ?, ?, ?)",
         (group_id, kind, str(item_id), top + 1))


def remove_from_group(group_id, kind, item_id):
    db.x("DELETE FROM group_items WHERE group_id = ? AND type = ? AND item_id = ?",
         (group_id, kind, str(item_id)))


def reorder_group(group_id, ordered):
    """ordered: lista med {type, id} i önskad ordning."""
    with db.tx() as conn:
        for index, ref in enumerate(ordered):
            conn.execute(
                "UPDATE group_items SET sort = ? WHERE group_id = ? AND type = ? AND item_id = ?",
                (index, group_id, ref["type"], str(ref["id"])),
            )
