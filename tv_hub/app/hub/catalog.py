"""Katalog: synk från Xtream, bläddring med sortering och filter, sök,
favoriter, egna grupper och kategoriinställningar.

Allt som boxarna ser går genom ett Scope (läge, profil, dolda kategorier).
Webbadminen använder ADMIN och ser allt.
"""
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
# Känner igen vuxenkategorier på namnet, t.ex. "XXX | Adult", "18+ Vuxen".
# Vuxeninnehåll avgörs bara av kategorin: namn med "For Adults", "XXX" eller "Porn".
# Titlar som "xXx" eller "Adult World" i vanliga kategorier räknas inte. Kategorier kan
# dessutom markeras eller avmarkeras manuellt i webbadminen (category_prefs.adult_override).
ADULT_RE = re.compile(r"(for\s*adults?|porn)", re.IGNORECASE)
ADULT_XXX = re.compile(r"(^|[|:\-]\s*)XXX(\s|$|[|:\-])")  # "XXX | ..." med versaler, inte filmen "xXx"
YEAR_IN_NAME = re.compile(r"(?:\(|\[|-\s)((?:19|20)\d{2})(?:\)|\]|\s*$)")
_sync_lock = threading.Lock()
_sync_state = {"running": False, "step": None}
_after_sync = []  # funktioner som körs efter varje lyckad synk (bevakningar m.m.)


# ====================================================================
# Scope: vad en viss box får se
# ====================================================================
class Scope:
    def __init__(self, adult=None, group_id=None, hide_hidden=False, history=True):
        self.adult = adult            # None = alla, 0 = vanligt läge, 1 = vuxenläge
        self.group_id = group_id      # profil "bara grupp X"
        self.hide_hidden = hide_hidden
        self.history = history        # vuxenläge: visa favoriter och historik

    @property
    def private(self):
        """Vuxenläge utan historik: favoriter, Fortsätt titta och sedda visas inte (men sparas)."""
        return bool(self.adult) and not self.history

    def sql(self, alias="i", hide=True):
        conds, args = [], []
        if self.adult is not None:
            conds.append(f"{alias}.adult = ?")
            args.append(1 if self.adult else 0)
        if self.group_id is not None:
            conds.append(
                f"EXISTS (SELECT 1 FROM group_items gx WHERE gx.group_id = ? "
                f"AND gx.type = {alias}.type AND gx.item_id = {alias}.item_id)"
            )
            args.append(self.group_id)
        elif self.hide_hidden and hide:
            conds.append(
                f"NOT EXISTS (SELECT 1 FROM category_prefs cpx WHERE cpx.type = {alias}.type "
                f"AND cpx.category_id = {alias}.category_id AND cpx.hidden = 1)"
            )
        return ("".join(f" AND {c}" for c in conds), args)


ADMIN = Scope()


# ====================================================================
# Hjälpare
# ====================================================================
def normalize(text):
    text = (text or "").casefold()
    text = re.sub(r"[|:_\-\[\]().,/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _category(raw):
    cid = raw.get("category_id")
    if cid in (None, "") and raw.get("category_ids"):
        cid = raw["category_ids"][0]
    return None if cid is None else str(cid)


def is_adult_name(text):
    return bool(text and (ADULT_RE.search(str(text)) or ADULT_XXX.search(str(text))))


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes")


def year_from(raw, name):
    for key in ("year", "releaseDate", "release_date", "releasedate"):
        value = str(raw.get(key) or "")
        match = re.match(r"((?:19|20)\d{2})", value)
        if match:
            return int(match.group(1))
    match = YEAR_IN_NAME.search(name or "")
    return int(match.group(1)) if match else None


def rating_from(raw):
    rating = _float(raw.get("rating"))
    if rating is not None and rating <= 10:
        return round(rating, 1)
    five = _float(raw.get("rating_5based"))
    return round(five * 2, 1) if five is not None and five <= 5 else None


def minutes_from(value):
    """'01:45:00', '105 min', '6300' (sekunder) eller 105 -> minuter."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    match = re.match(r"^(\d+):(\d{1,2})(?::(\d{1,2}))?$", text)
    if match:
        hours, mins = int(match.group(1)), int(match.group(2))
        if match.group(3) is None:  # mm:ss eller hh:mm, anta hh:mm om första > 0 och < 10
            return hours * 60 + mins if hours < 10 else hours
        return hours * 60 + mins
    match = re.match(r"^(\d+)\s*(min|m)?", text)
    if match:
        number = int(match.group(1))
        if match.group(2):
            return number
        return round(number / 60) if number > 400 else number
    return None


def _map_item(kind, raw):
    if kind in ("live", "vod"):
        item_id, icon = raw.get("stream_id"), raw.get("stream_icon")
    else:
        item_id, icon = raw.get("series_id"), raw.get("cover")
    if item_id in (None, ""):
        return None
    name = str(raw.get("name") or raw.get("title") or "").strip() or f"#{item_id}"
    archive_days = 0
    if kind == "live" and _truthy(raw.get("tv_archive")):
        archive_days = _int(raw.get("tv_archive_duration")) or 1
    return (
        kind, str(item_id), name, normalize(name), _category(raw), icon or None,
        raw.get("container_extension") or None,
        str(raw.get("rating") or "") or None,
        _int(raw.get("added") or raw.get("last_modified")),
        _int(raw.get("num")),
        raw.get("epg_channel_id") or None,
        0,  # sätts av mark_adult() utifrån kategorin
        year_from(raw, name) if kind != "live" else None,
        rating_from(raw) if kind != "live" else None,
        archive_days,
    )


def _series_info_row(raw):
    """Serielistan innehåller redan handling, genre m.m. Spara direkt."""
    sid = raw.get("series_id")
    if sid in (None, ""):
        return None
    run_time = raw.get("episode_run_time")
    backdrop = raw.get("backdrop_path")
    if isinstance(backdrop, list):
        backdrop = backdrop[0] if backdrop else None
    return (
        "series", str(sid), 1, db.iso(), minutes_from(run_time),
        raw.get("genre") or None, raw.get("plot") or None, raw.get("cast") or None,
        raw.get("director") or None, raw.get("releaseDate") or raw.get("release_date") or None,
        raw.get("cover") or None, backdrop or None, raw.get("youtube_trailer") or None, None,
        year_from(raw, raw.get("name") or ""), rating_from(raw),
    )


# ====================================================================
# Synk
# ====================================================================
def sync_status():
    return dict(_sync_state)


def start_sync(trigger="manual"):
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


def is_syncing():
    return _sync_lock.locked()


def _sync(trigger):
    run_id = db.x(
        "INSERT INTO sync_runs (started, status, trigger) VALUES (?, 'running', ?)",
        (db.iso(), trigger),
    ).lastrowid
    steps, tried = [], []
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

    mark_adult()
    failed = [s for s in steps if not s.get("ok") or s.get("kept_old")]
    status = "ok" if not failed else ("partial" if len(failed) < len(steps) else "failed")
    detail = {"steps": steps}
    _finish(run_id, portal["id"], status, detail)
    db.log_event("sync", {"status": status, "failed_steps": [f"{s['type']}/{s['part']}" for s in failed]})
    if status != "failed":
        for hook in _after_sync:
            try:
                hook()
            except Exception as exc:  # en trasig krok får inte stoppa synken
                print(f"[fel] efter synk: {exc}", flush=True)
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
            name = str(raw.get("category_name") or cid)
            rows.append((kind, str(cid), name, index, 1 if is_adult_name(name) else 0))
        existing = db.q1("SELECT COUNT(*) AS n FROM categories WHERE type = ?", (kind,))["n"]
        if not rows and existing:
            result.update(kept_old=True, error_kind="empty_list",
                          error="Källan returnerade en tom lista. Den gamla listan behölls.")
            return
        with db.tx() as conn:
            conn.execute("DELETE FROM categories WHERE type = ?", (kind,))
            conn.executemany(
                "INSERT OR REPLACE INTO categories (type, category_id, name, sort, adult) VALUES (?,?,?,?,?)", rows
            )
        return

    raws = [raw for raw in data if isinstance(raw, dict)]
    rows = [r for r in (_map_item(kind, raw) for raw in raws) if r]
    existing = db.q1("SELECT COUNT(*) AS n FROM items WHERE type = ?", (kind,))["n"]
    if not rows and existing:
        result.update(kept_old=True, error_kind="empty_list",
                      error="Källan returnerade en tom lista. Den gamla listan behölls.")
        return
    if existing >= 50 and len(rows) < existing * 0.5:
        result.update(
            kept_old=True, error_kind="shrunk",
            error=f"Källan gav {len(rows)} objekt mot tidigare {existing}. Den gamla listan behölls.",
        )
        return
    with db.tx() as conn:
        # Eget tilläggsdatum: när TV Hub först såg titeln. Titlar som fanns när funktionen kom
        # (eller vid första synken) har inget och visas med källans datum.
        old = {r[0]: r[1] for r in conn.execute("SELECT item_id, first_seen FROM items WHERE type = ?", (kind,))}
        now = int(time.time())
        rows = [r + ((old[r[1]] if r[1] in old else (now if old else None)),) for r in rows]
        conn.execute("DELETE FROM items WHERE type = ?", (kind,))
        conn.executemany(
            """INSERT OR REPLACE INTO items (type, item_id, name, name_norm, category_id, icon, ext,
               rating, added, num, epg_id, adult, year, rating_num, archive_days, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        if kind == "series":
            info_rows = [r for r in (_series_info_row(raw) for raw in raws) if r]
            conn.executemany(
                """INSERT OR REPLACE INTO item_info (type, item_id, state, fetched, duration_min, genre, plot,
                   actors, director, release, poster, backdrop, trailer, country, year, rating)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                info_rows,
            )
    result["stored"] = len(rows)


def mark_adult():
    """Markerar kategorier (manuellt val, annars namnet) och låter objekten följa sin kategori."""
    with db.tx() as conn:
        rows = conn.execute(
            """SELECT c.type, c.category_id, c.name, p.adult_override FROM categories c
               LEFT JOIN category_prefs p ON p.type = c.type AND p.category_id = c.category_id"""
        ).fetchall()
        for row in rows:
            adult = row["adult_override"] if row["adult_override"] is not None else (1 if is_adult_name(row["name"]) else 0)
            conn.execute("UPDATE categories SET adult = ? WHERE type = ? AND category_id = ?",
                         (adult, row["type"], row["category_id"]))
        conn.execute(
            """UPDATE items SET adult = COALESCE((SELECT c.adult FROM categories c WHERE c.type = items.type
                   AND c.category_id = items.category_id), 0)"""
        )


def backfill_years():
    """Årtal ur titeln för objekt som saknar det (t.ex. efter uppgradering)."""
    rows = db.q("SELECT type, item_id, name FROM items WHERE year IS NULL AND type IN ('vod', 'series')")
    updates = []
    for row in rows:
        match = YEAR_IN_NAME.search(row["name"] or "")
        if match:
            updates.append((int(match.group(1)), row["type"], row["item_id"]))
    if updates:
        with db.tx() as conn:
            conn.executemany("UPDATE items SET year = ? WHERE type = ? AND item_id = ?", updates)
    return len(updates)


def sync_loop(stop_event):
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


def counts(scope=ADMIN):
    result = {}
    cond, args = scope.sql("items")
    for kind in TYPES:
        result[kind] = {
            "items": db.q1(f"SELECT COUNT(*) AS n FROM items WHERE type = ?{cond}", (kind, *args))["n"],
            "categories": len(categories(kind, scope)) if scope is not ADMIN else
            db.q1("SELECT COUNT(*) AS n FROM categories WHERE type = ?", (kind,))["n"],
        }
    if scope is ADMIN:
        result["adult_items"] = db.q1("SELECT COUNT(*) AS n FROM items WHERE adult = 1")["n"]
        result["hidden_categories"] = db.q1("SELECT COUNT(*) AS n FROM category_prefs WHERE hidden = 1")["n"]
    return result


# ====================================================================
# Läsning
# ====================================================================
ITEM_COLS = ("i.type, i.item_id, i.name, i.category_id, i.icon, i.ext, i.rating, "
             "COALESCE(i.first_seen, i.added) AS added, i.num, i.epg_id, "
             "i.adult, i.archive_days, COALESCE(i.year, f.year) AS year, "
             "COALESCE(i.rating_num, f.rating) AS rating_num, f.duration_min, f.genre")
FROM_ITEMS = "items i LEFT JOIN item_info f ON f.type = i.type AND f.item_id = i.item_id"


def _item(row, favs=None):
    keys = row.keys()
    item = {
        "type": row["type"], "id": row["item_id"], "name": row["name"],
        "category_id": row["category_id"], "icon": row["icon"], "ext": row["ext"],
        "rating": row["rating"], "added": row["added"], "num": row["num"], "epg_id": row["epg_id"],
        "adult": bool(row["adult"]),
    }
    for key in ("year", "rating_num", "duration_min", "genre", "archive_days"):
        if key in keys:
            item[key] = row[key]
    if "category_name" in keys:
        item["category_name"] = row["category_name"]
    if favs is not None:
        item["favorite"] = (row["type"], row["item_id"]) in favs
    return item


def favorite_keys():
    return {(r["type"], r["item_id"]) for r in db.q("SELECT type, item_id FROM favorites")}


def categories(kind, scope=ADMIN):
    """Kategorier med antal objekt. Boxarna ser bara synliga kategorier i egen ordning."""
    if scope.group_id is not None:
        return []
    cond, args = scope.sql("i", hide=False)
    rows = db.rows(db.q(
        f"""SELECT c.category_id, c.name, c.sort, c.adult, p.adult_override,
                   COALESCE(p.hidden, 0) AS hidden, p.position,
                   (SELECT COUNT(*) FROM items i WHERE i.type = c.type AND i.category_id = c.category_id{cond}) AS count
            FROM categories c LEFT JOIN category_prefs p ON p.type = c.type AND p.category_id = c.category_id
            WHERE c.type = ?
            ORDER BY CASE WHEN p.position IS NULL THEN 1 ELSE 0 END, p.position, c.sort""",
        (*args, kind),
    ))
    for c in rows:
        c["adult"] = bool(c["adult"])
        c["hidden"] = bool(c["hidden"])
        c["auto_adult"] = is_adult_name(c["name"])
    if scope is ADMIN:
        return rows
    return [c for c in rows if c["count"] > 0 and not (scope.hide_hidden and c["hidden"])]


SORTS = {
    "added": "i.added IS NULL, i.added DESC, i.name",
    "name": "i.name COLLATE NOCASE",
    "year": "i.year IS NULL, i.year DESC, i.name",
    "rating": "i.rating_num IS NULL, i.rating_num DESC, i.name",
    "duration": "i.duration_min IS NULL, i.duration_min, i.name",
    "duration_desc": "i.duration_min IS NULL, i.duration_min DESC, i.name",
    "num": "i.num IS NULL, i.num, i.name",
}

# Alla kolumner som går att sortera och filtrera på, samlade i en vy (alias i).
BROWSE_FROM = """(SELECT i.type, i.item_id, i.name, i.name_norm, i.category_id, i.icon, i.ext, i.rating,
                         COALESCE(i.first_seen, i.added) AS added, i.num, i.epg_id, i.adult, i.archive_days,
                         COALESCE(i.year, f.year) AS year, COALESCE(i.rating_num, f.rating) AS rating_num,
                         f.duration_min, f.genre, c.name AS category_name
                  FROM items i
                  LEFT JOIN item_info f ON f.type = i.type AND f.item_id = i.item_id
                  LEFT JOIN categories c ON c.type = i.type AND c.category_id = i.category_id) i"""


def _filters(params):
    conds, args = [], []

    def num(key, cast=float):
        try:
            value = params.get(key)
            return None if value in (None, "") else cast(value)
        except (TypeError, ValueError):
            return None

    for key, column, op, cast in (
        ("min_rating", "i.rating_num", ">=", float),
        ("min_year", "i.year", ">=", int),
        ("max_year", "i.year", "<=", int),
        ("min_duration", "i.duration_min", ">=", int),
        ("max_duration", "i.duration_min", "<=", int),
    ):
        value = num(key, cast)
        if value is not None:
            conds.append(f"{column} {op} ?")
            args.append(value)
    genre = (params.get("genre") or "").strip()
    if genre:
        conds.append("i.genre LIKE ?")
        args.append(f"%{genre}%")
    for token in normalize(params.get("q") or "").split():
        conds.append("i.name_norm LIKE ?")
        args.append(f"%{token}%")
    return conds, args


def browse(kind, scope=ADMIN, category_id=None, params=None, offset=0, limit=300):
    """Lista med sortering och filter. params: sort, min_rating, min_year, max_year,
    min_duration, max_duration, genre, q. Returnerar (objekt, totalt antal)."""
    params = params or {}
    if kind not in TYPES:
        raise ValueError("Okänd typ")
    sort = params.get("sort") if params.get("sort") in SORTS else ("num" if kind == "live" else "added")
    where, args = ["i.type = ?"], [kind]
    if category_id not in (None, "", "all"):
        where.append("i.category_id = ?")
        args.append(str(category_id))
        scope_cond, scope_args = scope.sql("i", hide=False)
    else:
        scope_cond, scope_args = scope.sql("i")
    conds, cond_args = _filters(params)
    where_sql = " AND ".join(where + conds) + scope_cond
    all_args = args + cond_args + scope_args
    total = db.q1(f"SELECT COUNT(*) AS n FROM {BROWSE_FROM} WHERE {where_sql}", all_args)["n"]
    rows = db.q(
        f"SELECT * FROM {BROWSE_FROM} WHERE {where_sql} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
        (*all_args, int(limit), int(offset)),
    )
    favs = set() if scope.private else favorite_keys()
    return [_item(r, favs) for r in rows], total


def items_in_category(kind, category_id, offset=0, limit=200, scope=ADMIN):
    items, _ = browse(kind, scope, category_id, {}, offset, limit)
    return items


def recent(kind, limit=50, scope=ADMIN):
    items, _ = browse(kind, scope, None, {"sort": "added"}, 0, limit)
    return items


def search(query, kind=None, limit=100, offset=0, scope=ADMIN):
    tokens = normalize(query).split()
    if not tokens:
        return []
    results = []
    for k in ([kind] if kind in TYPES else TYPES):
        items, _ = browse(k, scope, None, {"q": query, "sort": "name"}, 0, limit)
        results += items
    first = tokens[0]
    joined = " ".join(tokens)

    def rank(item):
        norm = normalize(item["name"])
        exact = 0 if norm == joined else (1 if norm.startswith(first) else 2)
        return (exact, TYPES.index(item["type"]), norm)

    results.sort(key=rank)
    return results[offset:offset + limit]


def get_item(kind, item_id):
    row = db.q1(
        f"""SELECT {ITEM_COLS}, c.name AS category_name FROM {FROM_ITEMS}
            LEFT JOIN categories c ON c.type = i.type AND c.category_id = i.category_id
            WHERE i.type = ? AND i.item_id = ?""",
        (kind, str(item_id)),
    )
    return _item(row) if row else None


def in_scope(kind, item_id, scope):
    """Får boxen se/spela objektet?"""
    cond, args = scope.sql("i", hide=False)
    return db.q1(f"SELECT 1 FROM items i WHERE i.type = ? AND i.item_id = ?{cond}",
                 (kind, str(item_id), *args)) is not None


def genres(kind, scope=ADMIN, limit=40):
    cond, args = scope.sql("i")
    counts_by_genre = {}
    for row in db.q(
        f"SELECT f.genre FROM {FROM_ITEMS} WHERE i.type = ? AND f.genre IS NOT NULL{cond}", (kind, *args)
    ):
        for part in re.split(r"[,/|&]", row["genre"]):
            name = part.strip()
            if 2 <= len(name) <= 30:
                key = name.capitalize()
                counts_by_genre[key] = counts_by_genre.get(key, 0) + 1
    ranked = sorted(counts_by_genre.items(), key=lambda kv: -kv[1])[:limit]
    return [{"name": name, "count": count} for name, count in ranked]


def neighbours(kind, item_id):
    item = get_item(kind, item_id)
    if not item or kind != "live":
        return None, None
    rows = db.q("SELECT item_id FROM items WHERE type = 'live' AND category_id IS ? ORDER BY num, name",
                (item["category_id"],))
    ids = [r["item_id"] for r in rows]
    if item_id not in ids:
        return None, None
    index = ids.index(item_id)
    return (ids[index - 1] if index > 0 else ids[-1]), ids[(index + 1) % len(ids)]


# ====================================================================
# Kategoriinställningar (dölj, ordning)
# ====================================================================
def set_category_prefs(kind, changes):
    if kind not in TYPES:
        raise ValueError("Okänd typ")
    with db.tx() as conn:
        for change in changes:
            cid = str(change.get("category_id"))
            current = conn.execute(
                "SELECT hidden, position, adult_override FROM category_prefs WHERE type = ? AND category_id = ?",
                (kind, cid),
            ).fetchone()
            hidden = int(bool(change["hidden"])) if "hidden" in change else (current["hidden"] if current else 0)
            position = change["position"] if "position" in change else (current["position"] if current else None)
            if "adult" in change:
                # true/false = manuellt val, null = automatiskt efter namnet
                override = None if change["adult"] is None else int(bool(change["adult"]))
            else:
                override = current["adult_override"] if current else None
            conn.execute(
                """INSERT OR REPLACE INTO category_prefs (type, category_id, hidden, position, adult_override)
                   VALUES (?,?,?,?,?)""",
                (kind, cid, hidden, position, override),
            )
    if any("adult" in change for change in changes):
        mark_adult()


# ====================================================================
# Favoriter
# ====================================================================
def _resolved(rows, favs=None):
    out = []
    for row in rows:
        if row["name"] is None:
            out.append({"type": row["ref_type"], "id": row["ref_id"], "name": None, "missing": True})
            continue
        item = _item(row)
        item["favorite"] = True if favs is None else (row["type"], row["item_id"]) in favs
        item["missing"] = False
        out.append(item)
    return out


def favorites(scope=ADMIN):
    cond, args = scope.sql("i", hide=False)
    rows = db.q(
        f"""SELECT f0.type AS ref_type, f0.item_id AS ref_id, {ITEM_COLS}
            FROM favorites f0 LEFT JOIN items i ON i.type = f0.type AND i.item_id = f0.item_id
            LEFT JOIN item_info f ON f.type = i.type AND f.item_id = i.item_id
            WHERE 1 = 1{cond}
            ORDER BY CASE f0.type WHEN 'live' THEN 0 WHEN 'vod' THEN 1 ELSE 2 END, f0.added""",
        args,
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


# ====================================================================
# Grupper
# ====================================================================
def groups(with_items=False, scope=ADMIN):
    out = []
    favs = set() if scope.private else favorite_keys()
    for g in db.q("SELECT * FROM groups ORDER BY sort, name"):
        if scope.group_id is not None and g["id"] != scope.group_id:
            continue
        group = {"id": g["id"], "name": g["name"], "sort": g["sort"]}
        group["count"] = db.q1("SELECT COUNT(*) AS n FROM group_items WHERE group_id = ?", (g["id"],))["n"]
        if with_items or scope is not ADMIN:
            items = group_items(g["id"], favs, scope)
            if scope is not ADMIN:
                group["count"] = len(items)
                if not items:
                    continue
            if with_items:
                group["items"] = items
        out.append(group)
    return out


def group_items(group_id, favs=None, scope=ADMIN):
    favs = favs if favs is not None else favorite_keys()
    cond, args = scope.sql("i", hide=False)
    rows = db.q(
        f"""SELECT gi.type AS ref_type, gi.item_id AS ref_id, {ITEM_COLS}
            FROM group_items gi LEFT JOIN items i ON i.type = gi.type AND i.item_id = gi.item_id
            LEFT JOIN item_info f ON f.type = i.type AND f.item_id = i.item_id
            WHERE gi.group_id = ?{cond} ORDER BY gi.sort, i.name""",
        (group_id, *args),
    )
    return _resolved(rows, favs)


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
        conn.execute("UPDATE devices SET only_group_id = NULL WHERE only_group_id = ?", (group_id,))
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
    with db.tx() as conn:
        for index, ref in enumerate(ordered):
            conn.execute(
                "UPDATE group_items SET sort = ? WHERE group_id = ? AND type = ? AND item_id = ?",
                (index, group_id, ref["type"], str(ref["id"])),
            )
