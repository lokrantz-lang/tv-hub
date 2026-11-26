"""Sport-fliken: turneringsnamn, kanaler och bibliotek per sport, kanalexporten, världscupens stora kort (utan nätverk)."""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tvhub-sp-")

from hub import channelexport, db, sports, sportsets, worldcup  # noqa: E402
from hub.catalog import Scope  # noqa: E402

db.connect()
fails = 0


def check(name, cond, info=None):
    global fails
    print(("  ok   " if cond else "  FEL  ") + name + ("" if cond else f"  -> {info}"))
    fails += 0 if cond else 1


t = sportsets.tournament
check("Presidents Cup med tid, dag och kanal", t("14.00 Presidents Cup  Dag 3 golf 9.27 HBO Max SE") == "Presidents Cup", t("14.00 Presidents Cup  Dag 3 golf 9.27 HBO Max SE"))
check("RSM Classic utan Featured Groups", t("14.30 Featured Groups  The RSM Classic golf FHD 11.19") == "The RSM Classic", t("14.30 Featured Groups  The RSM Classic golf FHD 11.19"))
check("Huvudsändning tas bort", t("17.40 Huvudsändning The RSM Classic golf FHD 11.20 HBO Max SE") == "The RSM Classic")
check("TGL", t("02.00 Atlanta Drive GC . Boston Common") == "Atlanta Drive GC", t("02.00 Atlanta Drive GC . Boston Common"))

now = int(time.time())
db.x("INSERT INTO categories (type, category_id, name) VALUES ('live', 'us', 'Sports US'), ('vod', 'n1', 'N1 Sports Events Replays'), "
     "('vod', 'mv', 'Movies'), ('vod', 'xx', 'XXX')")


def item(kind, iid, name, cat, epg=None, added=0, adult=0):
    db.x("INSERT INTO items (type, item_id, name, name_norm, category_id, epg_id, added, adult) VALUES (?,?,?,?,?,?,?,?)",
         (kind, iid, name, name.casefold(), cat, epg, added, adult))


item("live", "g1", "US: Golf Channel HD", "us", "golf.us")
item("live", "g2", "UK: Sky Sports Golf", "us", "skygolf.uk")
item("live", "g3", "CZ: Golf Channel", "us", "golfcz")
item("live", "x1", "Minigolf TV", "us")
item("vod", "v1", "14.00 Presidents Cup  Dag 3 golf 9.27 HBO Max SE", "n1", added=now - 100)
item("vod", "v2", "17.40 Presidents Cup Dag 4 golf FHD 9.27 HBO Max SE", "n1", added=now - 50)
item("vod", "v3", "16.00 The RSM Classic golf FHD 11.19", "n1", added=now - 10)
item("vod", "v4", "Caddyshack golf comedy", "mv", added=now - 5000)
item("vod", "v5", "Golf adult", "xx", added=now, adult=1)
db.x("INSERT INTO epg (epg_key, start, end, title, title_norm) VALUES ('golf.us', ?, ?, 'BMW Australian PGA', 'bmw australian pga')",
     (now - 600, now + 600))

ids = [c["id"] for c in sportsets.dedicated("Golf")]
check("alla golfkanaler hittas, inte minigolf", set(ids) == {"g1", "g2", "g3"}, ids)
interest = sports.create_interest({"name": "Golf", "kind": "sport", "keywords": sportsets.SETS["Golf"]["keywords"],
                                   "exclude": sportsets.SETS["Golf"]["exclude"], "title_only": True})
iv = sports.get_interest(interest["id"] if isinstance(interest, dict) else interest)
check("intresset Golf hör till uppsättningen Golf", sportsets.set_for(iv) == "Golf")
scope = Scope.__new__(Scope) if False else None
from hub import catalog  # noqa: E402
scope = catalog.NORMAL if hasattr(catalog, "NORMAL") else sports.NORMAL
chans = sportsets.sport_channels("Golf", scope, now)
check("kanalraden: tre kanaler, den som sänder nu först", [c["id"] for c in chans][:1] == ["g1"] and len(chans) == 3, [c["id"] for c in chans])
check("kanalraden visar vad som sänds nu", chans[0]["now_title"] == "BMW Australian PGA")
lib = sportsets.library(iv, scope)
names = [g["name"] for g in lib]
check("biblioteket grupperas per turnering, nyast först", names[:2] == ["The RSM Classic", "Presidents Cup"], names)
pc = next((g for g in lib if g["name"] == "Presidents Cup"), None)
check("Presidents Cup har två delar, nyast först", pc and pc["count"] == 2 and pc["items"][0]["id"] == "v2", pc)
check("vuxeninnehåll kommer aldrig med", not any(i["id"] == "v5" for g in lib for i in g["items"]))

ex = channelexport.export(now)
raw = json.dumps(ex)
check("exporten har alla kanaler med kategori", ex["counts"]["live"] == 4 and ex["live"][0]["category"] == "Sports US", ex["counts"])
check("exporten visar programguide per kanal", any(c["name"] == "US: Golf Channel HD" and c["guide_programs"] == 1 for c in ex["live"]))
check("exporten har filmkategorier men inte vuxen", {c["name"] for c in ex["vod_categories"]} == {"N1 Sports Events Replays", "Movies"}, ex["vod_categories"])
check("exporten har inga länkar eller lösenord", "http" not in raw and "password" not in raw and "username" not in raw)

h = worldcup._countdown
check("nedräkning: minuter, timmar, dagar", h(now + 600, now) == "Om 10 min" and h(now + 2 * 3600, now) == "Om 2 h" and h(now - 5, now) == "Sänds nu")
ov = worldcup.overview(scope, None, worldcup._ts("2026-11-26", "08:00"))
check("stora kortet: Madrid när inget sänds", ov["hero"] and ov["hero"]["leg"] == "madrid", ov["hero"])
check("statusraden förklarar guiden", "TV4" in (ov["status_line"] or ""), ov["status_line"])

print("ALLT OK" if fails == 0 else f"{fails} FEL")
sys.exit(1 if fails else 0)
