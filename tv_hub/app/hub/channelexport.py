"""Kanallista att skicka vidare (till exempel till Claude för att anpassa sport och världscupen).

Innehåller bara namn, kategorier, EPG-id, arkivdagar och hur mycket programguide som finns. Aldrig portalens adress,
användarnamn, lösenord, stream-länkar eller bildlänkar. Vuxenkategorier räknas men namnges inte.
"""
import time

from . import VERSION, db


def _cats(kind):
    return {r["category_id"]: r["name"] for r in db.q("SELECT category_id, name FROM categories WHERE type = ?", (kind,))}


def export(now=None):
    now = int(now or time.time())
    guide = {r["epg_key"]: (r["n"], r["last"]) for r in db.q(
        "SELECT epg_key, COUNT(*) AS n, MAX(end) AS last FROM epg WHERE end > ? GROUP BY epg_key", (now,))}
    from . import sports
    live_cats = _cats("live")
    live = []
    adult_live = 0
    for r in db.q("""SELECT item_id, name, category_id, epg_id, archive_days, adult FROM items
                     WHERE type = 'live' ORDER BY category_id, num, name"""):
        if r["adult"]:
            adult_live += 1
            continue
        key = sports._epg_key({"id": r["item_id"], "epg_id": r["epg_id"]})
        n, last = guide.get(key, (0, None))
        live.append({"name": r["name"], "category": live_cats.get(r["category_id"], ""), "stream_id": r["item_id"],
                     "epg_id": r["epg_id"] or "", "archive_days": int(r["archive_days"] or 0),
                     "guide_programs": n, "guide_days_ahead": round((last - now) / 86400, 1) if last else 0})
    out = {"app": "TV Hub", "version": VERSION, "exported": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
           "note": "Utan inloggningsuppgifter, serveradress och länkar.",
           "counts": {"live": len(live), "live_adult_hidden": adult_live}, "live": live}
    for kind in ("vod", "series"):
        cats = _cats(kind)
        counts = {}
        for r in db.q("SELECT category_id, COUNT(*) AS n, MAX(adult) AS adult FROM items WHERE type = ? GROUP BY category_id", (kind,)):
            if r["adult"]:
                continue
            counts[cats.get(r["category_id"], r["category_id"] or "")] = r["n"]
        out[f"{kind}_categories"] = [{"name": k, "count": v} for k, v in sorted(counts.items())]
        out["counts"][kind] = sum(counts.values())
    return out
