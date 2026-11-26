"""Extern tablå (tv4.se): tolkning av sidan och inläggning under dina kanaler (inga nätverksanrop)."""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tvhub-eg-")

from hub import db, extguide, worldcup  # noqa: E402

db.connect()
fails = 0


def check(name, cond, info=None):
    global fails
    print(("  ok   " if cond else "  FEL  ") + name + ("" if cond else f"  -> {info}"))
    fails += 0 if cond else 1


PAGE = """<html><head><script>var x = "<li>09:99</li>";</script></head><body>
<section><img src="https://imageproxy.a2d.tv/?source=https%3A%2F%2Fassets.tv4.se%2Flogos%2Ftv4-logo.png&width=200" alt="TV4">
<ul><li><span>22:30</span><h3>Nyheterna</h3></li>
<li><span>23:00</span><h3>The Rookie</h3></li>
<li><span>00:45</span><h3>Elsbeth</h3></li><li><a href="/x">Se hela dagen</a></li></ul></section>
<section><img src="https://assets.tv4.se/logos/tv7-logo.png" alt="">
<ul><li><span>14:55</span><h3>Världscupen i hoppning: Madrid</h3><p>Direkt från Madrid. Kommentatorer: Anna Svensson</p></li>
<li>17:40 <h3>Mord i paradiset</h3></li>
<li><span>00:20</span><h3>Bonde söker fru</h3></li></ul></section>
<section><img src="x/tv12-logo.png" alt="TV12"><ul><li>10:00<h3>Barnprogram</h3></li></ul></section>
</body></html>"""

p = extguide.parse(PAGE, "2026-11-29")
check("kanalerna hittas (TV4, Sjuan via logotyp, TV12)", set(p) == {"TV4", "Sjuan", "TV12"}, list(p))
sj = p["Sjuan"]
check("Sjuan har tre program med rätt titlar", [r[1] for r in sj] == ["Världscupen i hoppning: Madrid", "Mord i paradiset", "Bonde söker fru"], sj)
check("klockslaget blir rätt epok", sj[0][0] == time.mktime((2026, 11, 29, 14, 55, 0, 0, 0, -1)), sj[0])
check("raden under rubriken blir beskrivning", "Direkt från Madrid" in sj[0][2], sj[0])
check("efter midnatt räknas som nästa dygn", sj[2][0] == time.mktime((2026, 11, 30, 0, 20, 0, 0, 0, -1)) and p["TV4"][2][0] == time.mktime((2026, 11, 30, 0, 45, 0, 0, 0, -1)), (sj[2], p["TV4"][2]))
check("klockslag i skript och 'Se hela dagen' ignoreras", len(p["TV4"]) == 3, p["TV4"])
progs = extguide.to_programs(sj)
check("slut är nästa programs start, sista får en timme", progs[0][1] == progs[1][0] and progs[2][1] - progs[2][0] == 3600, progs)
check("tom sida ger inga program", extguide.parse("<html><body>inget</body></html>", "2026-11-29") == {})
check("klockslag och titel på samma rad går också", extguide.parse('<img alt="Sjuan"><ul><li>20:00 Idol</li></ul>', "2026-11-29")["Sjuan"][0][1] == "Idol")

# inläggning under dina kanaler
def channel(cid, name, archive, epg):
    db.x("INSERT INTO items (type, item_id, name, name_norm, category_id, epg_id, archive_days) VALUES ('live', ?, ?, ?, 'c', ?, ?)",
         (cid, name, name.casefold(), epg, archive))
channel("1", "SE: TV4 HD", 7, "tv4.se")
channel("2", "SE: Sjuan HD", 0, "sjuan.se")
channel("3", "SE: TV4 Film", 7, "film.se")
calls = []
def fake(day):
    calls.append(day)
    return PAGE
now = int(time.mktime((2026, 11, 28, 9, 0, 0, 0, 0, -1)))
st = extguide.fill(fetcher=fake, now=now, days=2)
check("hämtade två dagar utan fel", st["days_ok"] == 2 and not st["errors"] and calls == ["2026-11-28", "2026-11-29"], st)
check("kanalerna kopplas till dina (inte TV4 Film)", st["targets"]["TV4"] == "SE: TV4 HD" and st["targets"]["Sjuan"] == "SE: Sjuan HD", st["targets"])
n = db.q1("SELECT COUNT(*) AS n FROM epg WHERE epg_key = 'sjuan.se'")["n"]
check("Sjuans program ligger i guiden", n >= 3, n)
row = db.q1("SELECT * FROM epg WHERE epg_key = 'tv4.se' LIMIT 1")
check("TV4 med arkiv får arkivflagga, Sjuan utan får ingen", row["archive"] == 1 and db.q1("SELECT archive FROM epg WHERE epg_key='sjuan.se' LIMIT 1")["archive"] == 0)
check("Tv4 Film fick inget", db.q1("SELECT COUNT(*) AS n FROM epg WHERE epg_key='film.se'")["n"] == 0)

# världscupen hittar sändningen i den inlagda tablån
legs = {l["id"]: l for l in worldcup.overview(now=now)["legs"]}
mad = legs["madrid"]
check("Madrid hittas nu i guiden (inte bara enligt tablå)", mad["events"] and not mad["events"][0].get("unconfirmed")
      and mad["events"][0]["channel"] == "SE: Sjuan HD" and mad["events"][0]["title"].startswith("Världscupen"), mad["events"])
check("rubriken säger att sändningen finns i guiden", mad["headline"] == "Sändning finns i guiden", mad["headline"])

# Toucans egen guide med rader långt framåt lämnas ifrån
db.x("INSERT INTO epg (epg_key, start, end, title, title_norm, description) VALUES ('tv4.se', ?, ?, 'Toucan-rad', 'toucan rad', '')", (now + 5 * 86400, now + 5 * 86400 + 3600))
db.set_setting(extguide.OWNED_KEY, [])
st = extguide.fill(fetcher=fake, now=now, days=1)
check("Toucans egen guide skrivs inte över", st["channels"]["TV4"]["source"] == "toucan", st["channels"])

# fel vid hämtning
def boom(day):
    raise OSError("nere")
st = extguide.fill(fetcher=boom, now=now, days=5)
check("fel redovisas och avbryter efter tre", len(st["errors"]) == 3 and st["days_ok"] == 0, st)
print("ALLT OK" if not fails else f"{fails} FEL")
sys.exit(1 if fails else 0)
