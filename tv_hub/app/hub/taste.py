"""Filmkväll: tittarlägen, tummar, logg över vad ni sett, Vill se och förslag.

Allt räknas ut här i tillägget. Boxarna och telefonen hämtar bara färdiga listor.

Tittarläge  t.ex. "Själv", "Med döttrarna". Har egna tummar, egen logg och egna regler.
work_key    samma titel i flera versioner (4K, svensk text, olika kategorier) får samma nyckel,
            så att den bara föreslås en gång och tummar gäller alla versioner.
Förslag     lokal poäng: betyg från källan + likhet med det ni gillat (genre, regissör,
            skådespelare, årtionde, längd, animerat) + Vill se. Sedda och nekade tas bort.
            Finns färska AI-förslag (ai.py) kommer de först, filtrerade med samma regler.

Gäller bara vanligt läge. Vuxeninnehåll loggas, betygsätts och föreslås aldrig här.
"""
import json
import math
import os
import re
import threading
import time
from collections import defaultdict

from . import catalog, db
from .catalog import Scope

NORMAL = Scope(adult=0, hide_hidden=True)
KINDS = ("vod", "series")
CACHE_TTL = 600
# Informationen om filmer hämtas i bakgrunden. Har mer kommit in läses katalogen om, men högst
# så här ofta (sekunder). Testerna sätter 0.
CACHE_MIN_AGE = float(os.environ.get("TASTE_CACHE_MIN_AGE", "120"))
TONIGHT_HOURS = 16
SEEN_DEDUP_HOURS = 12
VALUES = {-1: "Inte för oss", 1: "Bra", 2: "Älskade"}

_lock = threading.Lock()
_cache = {}          # kind -> {"at", "items", "by_key"}
_version = {"n": 0}  # ökas när tummar/logg ändras, så att förslagen räknas om


# ====================================================================
# Samma titel i flera versioner
# ====================================================================
TAG_PREFIX = re.compile(r"^(?:[\[(|]?\s*[A-Z0-9+]{2,6}\s*[\])|:\-]\s*)+")
BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
PAREN = re.compile(r"\(([^)]*)\)")
TAG_SUFFIX = re.compile(r"(\s[-|]\s*[A-Z0-9]{2,4}\s*)+$")
QUALITY = re.compile(r"\b(4k|uhd|fhd|hd|sd|hdr|hdr10|dv|atmos|remux|hevc|x26[45]|h26[45]|1080p?|720p?|2160p?|multi|sub|subs|dubbed|"
                     r"swe|swedish|svensk\w*|nordic|eng|english)\b", re.IGNORECASE)
YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
SWEDISH = re.compile(r"(\b(SE|SV|SWE|SWEDISH|NORDIC)\b|svensk|swedish|nordic)", re.IGNORECASE)
SWEDISH_STRICT = re.compile(r"(\b(SE|SV|SWE)\b|SWEDISH|NORDIC|[Ss]vensk|[Ss]wedish|[Nn]ordic)")


def clean_title(name):
    """"SE - Dune: Part Two (2024) [4K]" -> "dune part two". Årtal tas bort (läggs till separat)."""
    text = str(name or "")
    text = TAG_PREFIX.sub("", text)
    text = TAG_SUFFIX.sub("", text.strip())
    text = BRACKETS.sub(" ", text)
    text = PAREN.sub(lambda m: " " if not YEAR.fullmatch(m.group(1).strip()) else " " + m.group(1) + " ", text)
    text = re.sub(r"\s[-|]\s.*$", lambda m: "" if QUALITY.search(m.group(0)) and len(m.group(0)) < 25 else m.group(0), text)
    text = QUALITY.sub(" ", text)
    text = YEAR.sub(" ", text)
    return catalog.normalize(text)


def work_key(name, year=None):
    if year is None:
        match = YEAR.search(str(name or ""))
        year = int(match.group(1)) if match else None
    return f"{clean_title(name)}|{year or ''}"


def is_swedish(item):
    text = " ".join(str(item.get(k) or "") for k in ("name", "category_name"))
    return bool(SWEDISH_STRICT.search(text)) or "swed" in str(item.get("country") or "").lower() \
        or "sverige" in str(item.get("country") or "").lower()


def quality_rank(item):
    text = f"{item.get('name') or ''} {item.get('category_name') or ''}".upper()
    if re.search(r"\b(4K|UHD|2160P?)\b", text):
        return 3
    if re.search(r"\b(FHD|1080P?)\b", text):
        return 2
    if re.search(r"\bHD\b|720P?", text):
        return 1
    return 0


def version_pref():
    return db.get_setting("version_pref") or "swedish"


def best_version(versions):
    """Vilken version som visas: svensk text först (standard) eller högsta kvalitet först."""
    pref = version_pref()

    def rank(item):
        swe = 1 if is_swedish(item) else 0
        q = quality_rank(item)
        info = 1 if item.get("genre") or item.get("plot") else 0
        primary = (swe, q) if pref == "swedish" else (q, swe)
        return (*primary, info, item.get("rating_num") or 0)

    return max(versions, key=rank)


# ====================================================================
# Katalogen i minnet (bara vanligt läge)
# ====================================================================
GENRE_SPLIT = re.compile(r"\s*[,/&|]\s*")
ANIM = re.compile(r"anim|tecknad|tecknat|anime|cartoon", re.IGNORECASE)
KID_GENRE = re.compile(r"famil|family|barn|kids|children|kinder|anim|tecknad|tecknat", re.IGNORECASE)
KID_CATEGORY = re.compile(r"barn|kids|children|family|famil|disney|pixar|dreamworks|anim|tecknat|junior", re.IGNORECASE)
NOT_KIDS = re.compile(r"horror|skräck|thriller|crime|krim|war\b|krig|erotic|erotik|adult|gore|slasher", re.IGNORECASE)


def _genres(text):
    return [g.strip().lower() for g in GENRE_SPLIT.split(str(text or "")) if g.strip()]


def _features(item):
    feats = set()
    for g in _genres(item.get("genre"))[:4]:
        feats.add("g:" + g)
    year = item.get("year")
    if year:
        feats.add(f"d:{year // 10 * 10}")
    minutes = item.get("duration_min")
    if minutes:
        feats.add("l:short" if minutes < 95 else ("l:long" if minutes > 140 else "l:mid"))
    if item["anim"]:
        feats.add("anim")
    for actor in [a.strip() for a in str(item.get("actors") or "").split(",") if a.strip()][:3]:
        feats.add("p:" + actor.lower())
    for director in [d.strip() for d in str(item.get("director") or "").split(",") if d.strip()][:2]:
        feats.add("r:" + director.lower())
    return feats


def _load(kind):
    rows = db.q(
        f"""SELECT i.type, i.item_id, i.name, i.icon, i.ext, i.adult, i.category_id,
                   COALESCE(i.year, f.year) AS year, COALESCE(i.rating_num, f.rating) AS rating_num,
                   f.duration_min, f.genre, f.plot, f.actors, f.director, f.country, f.backdrop, f.trailer,
                   COALESCE(i.first_seen, i.added) AS added, i.first_seen, c.name AS category_name
            FROM items i
            LEFT JOIN item_info f ON f.type = i.type AND f.item_id = i.item_id
            LEFT JOIN categories c ON c.type = i.type AND c.category_id = i.category_id
            WHERE i.type = ? AND i.adult = 0
              AND NOT EXISTS (SELECT 1 FROM category_prefs p WHERE p.type = i.type
                              AND p.category_id = i.category_id AND p.hidden = 1)""",
        (kind,),
    )
    by_key = defaultdict(list)
    for row in rows:
        item = dict(row)
        item["key"] = work_key(item["name"], item["year"])
        text = f"{item.get('genre') or ''} {item.get('category_name') or ''}"
        item["anim"] = bool(ANIM.search(text))
        bad = bool(NOT_KIDS.search(str(item.get("genre") or "")) or NOT_KIDS.search(str(item.get("category_name") or "")))
        item["kids"] = (bool(KID_GENRE.search(str(item.get("genre") or "")))
                        or bool(KID_CATEGORY.search(str(item.get("category_name") or "")))) and not bad
        item["swedish"] = is_swedish(item)
        by_key[item["key"]].append(item)
    works = []
    for key, versions in by_key.items():
        best = dict(best_version(versions))
        # Uppgifter som bara finns i en annan version används också.
        for field in ("genre", "plot", "actors", "director", "duration_min", "rating_num", "backdrop", "trailer", "year"):
            if not best.get(field):
                for v in versions:
                    if v.get(field):
                        best[field] = v[field]
                        break
        best["anim"] = any(v["anim"] for v in versions)
        best["kids"] = any(v["kids"] for v in versions) and not any(
            NOT_KIDS.search(str(v.get("genre") or "")) for v in versions)
        best["swedish"] = any(v["swedish"] for v in versions)
        best["versions"] = len(versions)
        # Visningsnamn: versionen med minst märkning ("Dune: Part Two (2024)" före "SE - Dune ... [4K]").
        best["display"] = min((v["name"] for v in versions), key=lambda n: (len(n) - len(clean_title(n)), len(n)))
        best["first_seen"] = max((v["first_seen"] or 0) for v in versions) or None
        best["feats"] = _features(best)
        works.append(best)
    return {"at": time.time(), "sig": _signature(kind), "items": works, "by_key": {w["key"]: w for w in works},
            "by_id": {v["item_id"]: key for key, vs in by_key.items() for v in vs},
            "versions": dict(by_key)}


def _signature(kind):
    row = db.q1("SELECT (SELECT COUNT(*) FROM items WHERE type = ?) AS n, "
                "(SELECT COUNT(*) FROM item_info WHERE type = ? AND state = 1) AS m", (kind, kind))
    return (row["n"], row["m"])


def catalog_cache(kind):
    with _lock:
        entry = _cache.get(kind)
    if entry:
        age = time.time() - entry["at"]
        if age < CACHE_MIN_AGE or (age < CACHE_TTL and entry["sig"] == _signature(kind)):
            return entry
    entry = _load(kind)
    with _lock:
        _cache[kind] = entry
    return entry


def invalidate_catalog(*_args):
    """Listorna har uppdaterats (eller versionsvalet ändrats): läs in katalogen igen."""
    with _lock:
        _cache.clear()


def invalidate():
    """Tummar, logg eller regler har ändrats. Förslagen räknas alltid från databasen, så
    katalogen i minnet behöver inte läsas om."""
    _version["n"] += 1


catalog._after_sync.append(invalidate_catalog)


def key_for(kind, item_id):
    """work_key för ett objekt. None om det inte finns i vanligt läge."""
    cache = catalog_cache(kind)
    key = cache["by_id"].get(str(item_id))
    if key:
        return key
    item = catalog.get_item(kind, item_id)
    if not item or item.get("adult"):
        return None
    return work_key(item["name"], item.get("year"))


def versions_of(kind, item_id):
    key = key_for(kind, item_id)
    if not key:
        return []
    versions = catalog_cache(kind)["versions"].get(key, [])
    return [{"id": v["item_id"], "name": v["name"], "category_name": v.get("category_name"),
             "ext": v.get("ext"), "swedish": v["swedish"], "quality": quality_rank(v)}
            for v in sorted(versions, key=lambda v: (-quality_rank(v), v["name"]))]


# ====================================================================
# Tittarlägen
# ====================================================================
RULE_KEYS = ("kids", "max_minutes", "rewatch_days", "exclude_genres", "animated")


def _viewer(row):
    v = dict(row)
    # Profilens PIN lämnas aldrig ut, bara om den finns.
    v["has_pin"] = bool(v.pop("pin_hash", None))
    v.pop("pin_salt", None)
    try:
        v["rules"] = json.loads(v.get("rules") or "{}")
    except ValueError:
        v["rules"] = {}
    return v


def viewers():
    return [_viewer(r) for r in db.q("SELECT * FROM viewers ORDER BY position, id")]


def viewer(viewer_id):
    try:
        row = db.q1("SELECT * FROM viewers WHERE id = ?", (int(viewer_id),))
    except (TypeError, ValueError):
        return None
    return _viewer(row) if row else None


def default_viewer_id():
    row = db.q1("SELECT id FROM viewers ORDER BY position, id LIMIT 1")
    return row["id"] if row else None


def _clean_rules(rules):
    rules = rules if isinstance(rules, dict) else {}
    out = {}
    out["kids"] = bool(rules.get("kids"))
    for key in ("max_minutes", "rewatch_days"):
        try:
            value = int(rules.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        out[key] = max(0, min(value, 3650))
    genres = rules.get("exclude_genres") or []
    if isinstance(genres, str):
        genres = [g for g in re.split(r"\s*,\s*", genres) if g]
    out["exclude_genres"] = [str(g).strip().lower()[:40] for g in genres if str(g).strip()][:20]
    out["animated"] = rules.get("animated") if rules.get("animated") in ("any", "only", "none") else "any"
    return out


def save_viewer(data, viewer_id=None):
    name = str(data.get("name") or "").strip()[:40]
    if viewer_id is None and not name:
        raise ValueError("Namn krävs.")
    taste = str(data.get("taste") or "").strip()[:2000] if "taste" in data else None
    rules = _clean_rules(data.get("rules")) if "rules" in data else None
    if viewer_id is None:
        pos = (db.q1("SELECT MAX(position) AS m FROM viewers")["m"] or 0) + 1
        viewer_id = db.x("INSERT INTO viewers (name, position, taste, rules, created) VALUES (?,?,?,?,?)",
                         (name, pos, taste, json.dumps(rules or _clean_rules({})), db.iso())).lastrowid
    else:
        if not viewer(viewer_id):
            raise LookupError("Tittarläget finns inte.")
        if name:
            db.x("UPDATE viewers SET name = ? WHERE id = ?", (name, viewer_id))
        if taste is not None:
            db.x("UPDATE viewers SET taste = ? WHERE id = ?", (taste, viewer_id))
        if rules is not None:
            db.x("UPDATE viewers SET rules = ? WHERE id = ?", (json.dumps(rules), viewer_id))
        if "position" in data:
            db.x("UPDATE viewers SET position = ? WHERE id = ?", (int(data["position"]), viewer_id))
    invalidate()
    return viewer(viewer_id)


def delete_viewer(viewer_id):
    if len(viewers()) <= 1:
        raise ValueError("Minst ett tittarläge måste finnas.")
    with db.tx() as conn:
        for table in ("ratings", "hidden_picks", "ai_picks"):
            conn.execute(f"DELETE FROM {table} WHERE viewer_id = ?", (viewer_id,))
        conn.execute("UPDATE seen_log SET viewer_id = NULL WHERE viewer_id = ?", (viewer_id,))
        conn.execute("UPDATE wishlist SET viewer_id = NULL WHERE viewer_id = ?", (viewer_id,))
        conn.execute("DELETE FROM viewers WHERE id = ?", (viewer_id,))
    invalidate()


def resolve_viewer(value):
    """Tittarläge från appen (X-TVHub-Viewer). Okänt eller saknas = None."""
    if value in (None, ""):
        return None
    v = viewer(value)
    return v["id"] if v else None


# ====================================================================
# Tummar, logg, Vill se och dolda förslag
# ====================================================================
def _require(kind, item_id):
    if kind not in KINDS:
        raise ValueError("Bara filmer och serier.")
    if not catalog.in_scope(kind, str(item_id), Scope(adult=0)):
        raise LookupError("Finns inte (eller är vuxeninnehåll).")
    item = catalog.get_item(kind, item_id)
    return item, key_for(kind, item_id)


def rate(viewer_id, kind, item_id, value, seen=False, device_id=None):
    """value: 2 Älskade, 1 Bra, -1 Inte för oss, 0 = ta bort tummen."""
    if not viewer(viewer_id):
        raise LookupError("Okänt tittarläge.")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError("Ogiltigt betyg.")
    if value not in (-1, 0, 1, 2):
        raise ValueError("Ogiltigt betyg.")
    item, key = _require(kind, item_id)
    if value == 0:
        db.x("DELETE FROM ratings WHERE viewer_id = ? AND type = ? AND work_key = ?", (viewer_id, kind, key))
    else:
        db.x("INSERT OR REPLACE INTO ratings (viewer_id, type, work_key, item_id, title, value, at) VALUES (?,?,?,?,?,?,?)",
             (viewer_id, kind, key, str(item_id), item["name"], value, db.iso()))
    if seen or value != 0:
        # En tumme betyder att ni har sett den. Finns den redan i loggen för läget läggs inget till.
        already = db.q1("SELECT 1 FROM seen_log WHERE viewer_id = ? AND type = ? AND work_key = ?", (viewer_id, kind, key))
        if seen or not already:
            log_seen(viewer_id, kind, item_id, "rating", device_id, claim=True)
    invalidate()
    return {"value": value, "work_key": key}


def rating_of(viewer_id, kind, key):
    if not viewer_id or not key:
        return 0
    row = db.q1("SELECT value FROM ratings WHERE viewer_id = ? AND type = ? AND work_key = ?", (viewer_id, kind, key))
    return row["value"] if row else 0


def log_seen(viewer_id, kind, item_id, source, device_id=None, claim=False, at=None):
    """Loggar att titeln setts. claim=True: en nyligen loggad rad utan tittarläge får detta läge
    (frågan "Vem tittade?" efter filmen)."""
    item = catalog.get_item(kind, item_id)
    if not item or item.get("adult"):
        return None
    key = key_for(kind, item_id)
    if not key:
        return None
    since = db.ago(hours=SEEN_DEDUP_HOURS)
    if claim and viewer_id:
        row = db.q1("SELECT id FROM seen_log WHERE type = ? AND work_key = ? AND viewer_id IS NULL AND at >= ? "
                    "ORDER BY id DESC LIMIT 1", (kind, key, since))
        if row:
            db.x("UPDATE seen_log SET viewer_id = ? WHERE id = ?", (viewer_id, row["id"]))
            invalidate()
            return row["id"]
    if at is None:
        dup = db.q1("SELECT id FROM seen_log WHERE type = ? AND work_key = ? AND viewer_id IS ? AND at >= ?",
                    (kind, key, viewer_id, since))
        if dup:
            return dup["id"]
    rid = db.x("INSERT INTO seen_log (viewer_id, type, work_key, item_id, title, at, source, device_id) VALUES (?,?,?,?,?,?,?,?)",
               (viewer_id, kind, key, str(item_id), item["name"], at or db.iso(), source, device_id)).lastrowid
    invalidate()
    return rid


def on_finished(session):
    """Från extras.update_progress: en film eller ett avsnitt har setts klart (vanligt läge)."""
    if session["adult"]:
        return
    keys = session.keys()
    viewer_id = session["viewer_id"] if "viewer_id" in keys else None
    try:
        if session["type"] == "vod":
            log_seen(viewer_id, "vod", session["item_id"], "auto", session["device_id"])
        elif session["type"] == "episode" and session["series_id"]:
            log_seen(viewer_id, "series", session["series_id"], "auto", session["device_id"])
    except Exception as exc:  # loggen får aldrig störa uppspelningen
        print(f"[fel] logg över sett: {exc!r}", flush=True)


def seen_log(limit=200, viewer_id=None):
    sql = """SELECT s.*, v.name AS viewer_name FROM seen_log s LEFT JOIN viewers v ON v.id = s.viewer_id"""
    args = []
    if viewer_id:
        sql += " WHERE s.viewer_id = ?"
        args.append(viewer_id)
    sql += " ORDER BY s.at DESC, s.id DESC LIMIT ?"
    args.append(int(limit))
    return db.rows(db.q(sql, args))


def delete_seen(row_id):
    db.x("DELETE FROM seen_log WHERE id = ?", (row_id,))
    invalidate()


def set_wish(kind, item_id, on, viewer_id=None):
    item, key = _require(kind, item_id)
    if on:
        db.x("INSERT OR REPLACE INTO wishlist (type, work_key, item_id, title, viewer_id, added) VALUES (?,?,?,?,?,?)",
             (kind, key, str(item_id), item["name"], viewer_id, db.iso()))
    else:
        db.x("DELETE FROM wishlist WHERE type = ? AND work_key = ?", (kind, key))
    invalidate()
    return bool(on)


def wishlist():
    out = []
    for row in db.q("""SELECT w.*, v.name AS viewer_name FROM wishlist w LEFT JOIN viewers v ON v.id = w.viewer_id
                       ORDER BY w.added DESC"""):
        item = catalog.get_item(row["type"], row["item_id"])
        entry = dict(row)
        entry["item"] = item
        out.append(entry)
    return out


def in_wishlist(kind, key):
    return bool(key and db.q1("SELECT 1 FROM wishlist WHERE type = ? AND work_key = ?", (kind, key)))


def hide(viewer_id, kind, item_id, forever):
    _, key = _require(kind, item_id)
    until = None if forever else int(time.time() + TONIGHT_HOURS * 3600)
    db.x("INSERT OR REPLACE INTO hidden_picks (viewer_id, type, work_key, until) VALUES (?,?,?,?)",
         (viewer_id, kind, key, until))
    invalidate()


def ratings_list(viewer_id=None, limit=500):
    sql = "SELECT r.*, v.name AS viewer_name FROM ratings r JOIN viewers v ON v.id = r.viewer_id"
    args = []
    if viewer_id:
        sql += " WHERE r.viewer_id = ?"
        args.append(viewer_id)
    sql += " ORDER BY r.at DESC LIMIT ?"
    args.append(limit)
    return db.rows(db.q(sql, args))


# ====================================================================
# Förslag
# ====================================================================
def _profile(viewer_id, kind, cache):
    """Vikter per egenskap utifrån tummar (och sett klart utan tumme som ett litet plus)."""
    weights = defaultdict(float)
    counts = defaultdict(int)
    examples = {}  # egenskap -> titel ni gillat, för motiveringen
    rated = {}
    for row in db.q("SELECT work_key, value, title FROM ratings WHERE viewer_id = ? AND type = ?", (viewer_id, kind)):
        rated[row["work_key"]] = row["value"]
    seen_keys = {r["work_key"] for r in db.q(
        "SELECT DISTINCT work_key FROM seen_log WHERE type = ? AND viewer_id = ?", (kind, viewer_id))}
    signals = {k: {2: 1.5, 1: 1.0, -1: -1.5}[v] for k, v in rated.items()}
    for key in seen_keys:
        signals.setdefault(key, 0.3)
    for key, signal in signals.items():
        work = cache["by_key"].get(key)
        if not work:
            continue
        for feat in work["feats"]:
            weights[feat] += signal
            counts[feat] += 1
            if signal >= 1 and (feat not in examples or signal > examples[feat][0]):
                examples[feat] = (signal, work["name"])
    for feat in list(weights):
        weights[feat] = weights[feat] / math.sqrt(counts[feat] + 1)
    return weights, {f: t for f, (s, t) in examples.items()}, rated, len(signals)


def _excluded(viewer_id, kind, rules):
    """work_keys som inte ska föreslås: sedda, nekade (-1), Aldrig och Inte i kväll."""
    now = time.time()
    out = set()
    is_default = viewer_id == default_viewer_id()
    love = {r["work_key"] for r in db.q("SELECT work_key FROM ratings WHERE viewer_id = ? AND type = ? AND value = 2",
                                        (viewer_id, kind))}
    rewatch = rules.get("rewatch_days") or 0
    last_seen = {}
    for row in db.q("""SELECT work_key, MAX(at) AS last FROM seen_log
                       WHERE type = ? AND (viewer_id = ? OR (viewer_id IS NULL AND ?)) GROUP BY work_key""",
                    (kind, viewer_id, 1 if is_default else 0)):
        last_seen[row["work_key"]] = row["last"]
    cutoff = db.ago(days=rewatch) if rewatch else None
    for key, last in last_seen.items():
        if rewatch and key in love and last < cutoff:
            continue  # älskade och sett för länge sedan: får föreslås igen
        out.add(key)
    for row in db.q("SELECT work_key FROM ratings WHERE viewer_id = ? AND type = ? AND value < 0", (viewer_id, kind)):
        out.add(row["work_key"])
    for row in db.q("SELECT work_key, until FROM hidden_picks WHERE viewer_id = ? AND type = ?", (viewer_id, kind)):
        if row["until"] is None or row["until"] > now:
            out.add(row["work_key"])
    # Påbörjade finns redan under Fortsätt titta.
    if kind == "vod":
        cache = catalog_cache(kind)
        for row in db.q("SELECT item_id FROM progress WHERE adult = 0 AND type = 'vod'"):
            key = cache["by_id"].get(row["item_id"])
            if key:
                out.add(key)
    return out, love, last_seen


def parse_filters(params, rules):
    """Snabbvalen. Saknas ett val används tittarlägets regel."""
    def flag(name, default=False):
        value = params.get(name)
        if value in (None, ""):
            return default
        return str(value).lower() in ("1", "true", "yes", "on")

    anim = params.get("anim") if params.get("anim") in ("any", "only", "none") else rules.get("animated", "any")
    try:
        max_minutes = int(params.get("max") or 0) or (rules.get("max_minutes") or 0)
    except (TypeError, ValueError):
        max_minutes = rules.get("max_minutes") or 0
    return {
        "anim": anim,
        "max": max_minutes,
        "new": flag("new"),
        "high": flag("high"),
        "swedish": flag("swe"),
        "kids": flag("kids", bool(rules.get("kids"))),
    }


def _passes(work, filters, rules):
    if filters["anim"] == "only" and not work["anim"]:
        return False
    if filters["anim"] == "none" and work["anim"]:
        return False
    if filters["max"] and (work.get("duration_min") or 0) > filters["max"]:
        return False
    if filters["new"] and not work.get("first_seen"):
        return False
    if filters["high"] and (work.get("rating_num") or 0) < 7:
        return False
    if filters["swedish"] and not work["swedish"]:
        return False
    if filters["kids"] and not work["kids"]:
        return False
    genre = str(work.get("genre") or "").lower()
    if any(g and g in genre for g in rules.get("exclude_genres") or []):
        return False
    return True


def _facts(work):
    parts = []
    if work.get("year"):
        parts.append(str(work["year"]))
    if work.get("duration_min"):
        m = work["duration_min"]
        parts.append(f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min")
    if work.get("rating_num"):
        parts.append("★ " + f"{work['rating_num']:.1f}".replace(".", ","))
    genres = _genres(work.get("genre"))
    if genres:
        parts.append(", ".join(g.capitalize() for g in genres[:2]))
    return " · ".join(parts)


def _reason(work, weights, examples, wish, love_again):
    if love_again:
        return "Ni älskade den, och det var länge sedan"
    if wish:
        return "Från er Vill se-lista"
    best, best_w = None, 0.25
    for feat in work["feats"]:
        w = weights.get(feat, 0)
        if feat.startswith(("r:", "p:")):
            w *= 1.5  # regissör och skådespelare är mer träffande motiveringar
        if w > best_w:
            best, best_w = feat, w
    if best:
        example = examples.get(best)
        name = best.split(":", 1)[1] if ":" in best else best
        if best.startswith("r:"):
            return f"Samma regissör som {example}" if example else f"Regi: {name.title()}"
        if best.startswith("p:"):
            return f"Med {name.title()}, som i {example}" if example else f"Med {name.title()}"
        if best == "anim":
            return f"Animerad, som {example}" if example else "Animerad"
        if best.startswith("g:"):
            return f"Ni gillar {name}" + (f", som {example}" if example else "")
        if best.startswith("d:"):
            return f"{name}-tal, som {example}" if example else f"Från {name}-talet"
        if best.startswith("l:"):
            return {"short": "Kort, som ni brukar gilla", "mid": "Lagom lång", "long": "Lång, som ni brukar gilla"}[name]
    if (work.get("rating_num") or 0) >= 7.5:
        return "Högt betyg"
    if work.get("first_seen"):
        return "Nytt i listan"
    return "Kan passa er"


def suggest(viewer_id, kind="vod", params=None, limit=10, exclude=()):
    """Topp-listan för ett tittarläge. Returnerar {"items", "viewer", "filters", "ai", "basis"}."""
    v = viewer(viewer_id) or viewer(default_viewer_id())
    if v is None:
        raise LookupError("Inga tittarlägen.")
    kind = kind if kind in KINDS else "vod"
    rules = v["rules"]
    filters = parse_filters(params or {}, rules)
    cache = catalog_cache(kind)
    weights, examples, rated, basis = _profile(v["id"], kind, cache)
    excluded, love, last_seen = _excluded(v["id"], kind, rules)
    excluded |= {cache["by_id"].get(str(i), str(i)) for i in exclude}
    wishes = {r["work_key"]: r["viewer_id"] for r in db.q("SELECT work_key, viewer_id FROM wishlist WHERE type = ?", (kind,))}
    new_cutoff = time.time() - 30 * 86400

    scored = []
    for work in cache["items"]:
        key = work["key"]
        if key in excluded or not _passes(work, filters, rules):
            continue
        quality = ((work.get("rating_num") or 6.0) - 6.0) / 2.0
        affinity = sum(weights.get(f, 0.0) for f in work["feats"]) / math.sqrt(len(work["feats"]) + 1)
        score = 0.6 * quality + affinity
        wish = key in wishes and wishes[key] in (None, v["id"])
        if wish:
            score += 2.5
        if (work.get("first_seen") or 0) > new_cutoff:
            score += 0.3
        if not work.get("genre"):
            score -= 0.5  # vet nästan inget om titeln
        love_again = key in love and key in last_seen
        if love_again:
            score += 0.8
        scored.append((score, work, wish, love_again))
    scored.sort(key=lambda s: -s[0])

    # AI-förslag (ai.py) först, med samma filter. Sedan lokala, med variation i genre.
    picks, used = [], set()
    ai_used = False
    from . import ai
    ai_list = ai.current_picks(v["id"], kind)
    if ai_list:
        by_key = {w["key"]: (s, w, wish, again) for s, w, wish, again in scored}
        for pick in ai_list:
            key = cache["by_id"].get(str(pick["id"])) or pick.get("key")
            if key in by_key and key not in used:
                s, w, wish, again = by_key[key]
                picks.append(_out(w, pick.get("reason") or _reason(w, weights, examples, wish, again), rated, "ai"))
                used.add(key)
                ai_used = True
            if len(picks) >= limit:
                break
    genre_count = defaultdict(int)
    for item in picks:
        genre_count[item["_genre"]] += 1
    pool = scored[: max(200, limit * 20)]
    while len(picks) < limit and pool:
        best_i, best_s = None, None
        for i, (s, w, wish, again) in enumerate(pool):
            if w["key"] in used:
                continue
            g = (_genres(w.get("genre")) or ["?"])[0]
            adjusted = s - 0.35 * genre_count[g]
            if best_s is None or adjusted > best_s:
                best_i, best_s = i, adjusted
            if i > 300:
                break
        if best_i is None:
            break
        s, w, wish, again = pool.pop(best_i)
        used.add(w["key"])
        item = _out(w, _reason(w, weights, examples, wish, again), rated, "local")
        genre_count[item["_genre"]] += 1
        picks.append(item)
    for item in picks:
        item.pop("_genre", None)
    return {
        "viewer": {"id": v["id"], "name": v["name"]},
        "kind": kind,
        "filters": filters,
        "items": picks,
        "ai": ai_used,
        "basis": basis,
        "candidates": len(scored),
    }


def _out(work, reason, rated, source):
    return {
        "type": work["type"], "id": work["item_id"], "name": work.get("display") or work["name"], "icon": work.get("icon"),
        "ext": work.get("ext"), "year": work.get("year"), "rating_num": work.get("rating_num"),
        "duration_min": work.get("duration_min"), "genre": work.get("genre"), "plot": work.get("plot"),
        "backdrop": work.get("backdrop"), "category_name": work.get("category_name"),
        "reason": reason, "facts": _facts(work), "versions": work.get("versions", 1),
        "my_rating": rated.get(work["key"], 0), "source": source, "key": work["key"],
        "_genre": (_genres(work.get("genre")) or ["?"])[0],
    }


def candidates_for_ai(viewer_id, kind, limit=80):
    """De bästa lokala kandidaterna, utan AI-förslag, som underlag till AI-tjänsten."""
    v = viewer(viewer_id)
    rules = v["rules"] if v else {}
    filters = parse_filters({}, rules)
    cache = catalog_cache(kind)
    weights, examples, rated, basis = _profile(viewer_id, kind, cache)
    excluded, love, last_seen = _excluded(viewer_id, kind, rules)
    scored = []
    for work in cache["items"]:
        if work["key"] in excluded or not _passes(work, filters, rules):
            continue
        quality = ((work.get("rating_num") or 6.0) - 6.0) / 2.0
        affinity = sum(weights.get(f, 0.0) for f in work["feats"]) / math.sqrt(len(work["feats"]) + 1)
        scored.append((0.6 * quality + affinity + (0.5 if work.get("genre") else -1), work))
    scored.sort(key=lambda s: -s[0])
    return [w for _, w in scored[:limit]]


def detail_extras(kind, item_id, viewer_id):
    """Till detaljsidan: versioner, Vill se och tummen för tittarläget."""
    key = key_for(kind, item_id)
    vid = viewer_id or default_viewer_id()
    return {
        "versions": versions_of(kind, item_id) if kind == "vod" else [],
        "wish": in_wishlist(kind, key),
        "my_rating": rating_of(vid, kind, key),
        "viewer_id": vid,
    }


# ====================================================================
# Import från IMDb och Letterboxd
# ====================================================================
def import_csv(text, viewer_id):
    """IMDb (ratings.csv) eller Letterboxd (ratings.csv/watched.csv). Matchar på titel och årtal."""
    import csv
    import io
    if not viewer(viewer_id):
        raise LookupError("Okänt tittarläge.")
    reader = csv.DictReader(io.StringIO(str(text or "").lstrip("﻿")))
    fields = [f.strip() for f in (reader.fieldnames or [])]
    if not fields:
        raise ValueError("Filen är tom.")
    lower = {f.lower(): f for f in fields}
    if "const" in lower and "your rating" in lower:
        fmt = "imdb"
    elif "letterboxd uri" in lower or ("name" in lower and "year" in lower):
        fmt = "letterboxd"
    elif "title" in lower and "year" in lower:
        fmt = "generic"
    else:
        raise ValueError("Okänt format. Använd IMDb:s eller Letterboxds export (CSV).")
    index = {}
    for kind in KINDS:
        cache = catalog_cache(kind)
        for work in cache["items"]:
            title_key = clean_title(work["name"])
            index.setdefault((kind, title_key, work.get("year")), work)
            index.setdefault((kind, title_key, None), work)
    matched, unmatched, rated_n = 0, [], 0
    for raw in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        title = row.get("title") or row.get("name") or row.get("original title")
        if not title:
            continue
        try:
            year = int((row.get("year") or "")[:4]) if row.get("year") else None
        except ValueError:
            year = None
        kinds = ["series"] if "series" in (row.get("title type") or "").lower() else ["vod", "series"]
        work = None
        for kind in kinds:
            title_key = clean_title(title)
            work = index.get((kind, title_key, year)) or (index.get((kind, title_key, None)) if year is None else None)
            if not work and year:
                for dy in (-1, 1):
                    work = index.get((kind, title_key, year + dy))
                    if work:
                        break
            if work:
                break
        if not work:
            if len(unmatched) < 30:
                unmatched.append(f"{title} ({year})" if year else title)
            continue
        matched += 1
        date = row.get("date rated") or row.get("watched date") or row.get("date") or ""
        at = f"{date[:10]}T12:00:00" if re.match(r"\d{4}-\d{2}-\d{2}", date) else db.iso()
        kind = work["type"]
        exists = db.q1("SELECT 1 FROM seen_log WHERE viewer_id = ? AND type = ? AND work_key = ? AND source = 'import'",
                       (viewer_id, kind, work["key"]))
        if not exists:
            db.x("INSERT INTO seen_log (viewer_id, type, work_key, item_id, title, at, source) VALUES (?,?,?,?,?,?,?)",
                 (viewer_id, kind, work["key"], work["item_id"], work["name"], at, "import"))
        value = None
        try:
            if fmt == "imdb" and row.get("your rating"):
                r = int(float(row["your rating"]))
                value = 2 if r >= 8 else (1 if r >= 6 else -1)
            elif row.get("rating"):
                r = float(row["rating"])
                value = 2 if r >= 4 else (1 if r >= 3 else -1)
        except ValueError:
            value = None
        if value is not None:
            rated_n += 1
            db.x("INSERT OR REPLACE INTO ratings (viewer_id, type, work_key, item_id, title, value, at) VALUES (?,?,?,?,?,?,?)",
                 (viewer_id, kind, work["key"], work["item_id"], work["name"], value, at))
    invalidate()
    return {"format": fmt, "matched": matched, "rated": rated_n, "unmatched": unmatched}
