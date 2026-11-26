"""Toucans guidefil (XMLTV): tolkning, gzip, fyller bara kanaler utan program (utan nätverk)."""
import gzip
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tvhub-xt-")

from hub import db, xmltvguide  # noqa: E402

db.connect()
fails = 0


def check(name, cond, info=None):
    global fails
    print(("  ok   " if cond else "  FEL  ") + name + ("" if cond else f"  -> {info}"))
    fails += 0 if cond else 1


check("tid med tidszon", xmltvguide._ts("20261126143000 +0100") == xmltvguide._ts("20261126133000 +0000"))
check("fel format ger None", xmltvguide._ts("igår") is None)

now = int(time.time())
fmt = lambda t: time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(t))  # noqa: E731
xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
<channel id="viasatgolf.se"><display-name>V Sport Golf</display-name></channel>
<channel id="tv4.se"><display-name>TV4</display-name></channel>
<channel id="annan.xx"><display-name>Annan</display-name></channel>
<programme start="{fmt(now + 3600)}" stop="{fmt(now + 7200)}" channel="viasatgolf.se"><title>DP World Tour: Dubai</title><desc>Golf</desc></programme>
<programme start="{fmt(now + 7200)}" stop="{fmt(now + 9000)}" channel="ViasatGolf.se"><title>Golf: PGA</title></programme>
<programme start="{fmt(now + 3600)}" stop="{fmt(now + 7200)}" channel="tv4.se"><title>Ska inte skrivas</title></programme>
<programme start="{fmt(now + 3600)}" stop="{fmt(now + 7200)}" channel="annan.xx"><title>Inte min kanal</title></programme>
<programme start="{fmt(now + 3600)}" stop="{fmt(now + 7200)}" channel="golfchannel.th"><title>Asian Tour</title></programme>
<programme start="{fmt(now + 40 * 86400)}" stop="{fmt(now + 40 * 86400 + 60)}" channel="viasatgolf.se"><title>För långt fram</title></programme>
</tv>""".encode()

db.x("INSERT INTO categories (type, category_id, name) VALUES ('live', 'se', 'Sweden')")
for iid, name, epg in (("1", "V Sport Golf SE", "viasatgolf.se"), ("2", "TV4 SE", "tv4.se")):
    db.x("INSERT INTO items (type, item_id, name, name_norm, category_id, epg_id, archive_days) VALUES ('live', ?, ?, ?, 'se', ?, 1)",
         (iid, name, name.casefold(), epg))
db.x("INSERT INTO epg (epg_key, start, end, title, title_norm) VALUES ('tv4.se', ?, ?, 'Nyheterna', 'nyheterna')", (now + 3600, now + 7200))
# Golfkanal i en kategori som genomsökningen inte tar med, och en som inte finns i filen.
db.x("INSERT INTO categories (type, category_id, name) VALUES ('live', 'th', 'Thailand')")
db.x("INSERT INTO items (type, item_id, name, name_norm, category_id, epg_id, archive_days) VALUES ('live', '3', 'Golf Channel TH', 'golf channel th', 'th', 'golfchannel.th', 0)")
db.x("INSERT INTO items (type, item_id, name, name_norm, category_id, epg_id, archive_days) VALUES ('live', '4', 'Sky Sports Golf UK', 'sky sports golf uk', 'th', 'skysportsgolf.uk', 0)")
db.x("INSERT INTO items (type, item_id, name, name_norm, category_id, epg_id, archive_days) VALUES ('live', '5', ':Golf  02', 'golf 02', 'th', 'event.02', 0)")


class Client:
    base, username, password = "http://x", "u", "p"


class Res(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


calls = []


def opener(req, timeout=None):
    calls.append(req.full_url)
    return Res(gzip.compress(xml))


st = xmltvguide.fill(force=True, now=now, client=Client(), opener=opener)
check("hämtningen lyckas och gzip packas upp", st["ok"] and st["channels_in_file"] == 3 and st["programs_in_file"] == 6, st)
rows = db.q("SELECT title FROM epg WHERE epg_key = 'viasatgolf.se' ORDER BY start")
check("golfkanalen får program (skiftläge spelar ingen roll), inte för långt fram", [r["title"] for r in rows] == ["DP World Tour: Dubai", "Golf: PGA"], [r["title"] for r in rows])
check("kanal som redan har program skrivs inte över", [r["title"] for r in db.q("SELECT title FROM epg WHERE epg_key = 'tv4.se'")] == ["Nyheterna"])
check("kanaler som inte är dina hämtas inte in", not db.q("SELECT 1 FROM epg WHERE epg_key = 'annan.xx'"))
check("arkivflaggan följer kanalen", db.q1("SELECT archive FROM epg WHERE epg_key = 'viasatgolf.se'")["archive"] == 1)
check("statusen visar kanalnamn", st["filled"] == 2 and "V Sport Golf SE" in st["examples"], st)
golf = {c["name"]: c for c in st["sport"]}
check("svaret per golfkanal: finns i filen och har program", golf.get("V Sport Golf SE", {}).get("in_file") and golf["V Sport Golf SE"]["programs"] == 2, st["sport"])
check("adressen till guidefilen", calls and "/xmltv.php?" in calls[0])
check("golfkanal utanför genomsökningen fylls ändå", [r["title"] for r in db.q("SELECT title FROM epg WHERE epg_key = 'golfchannel.th'")] == ["Asian Tour"])
check("golfkanal som saknas i filen visas som saknad", golf.get("Sky Sports Golf UK", {}).get("in_file") is False, golf.get("Sky Sports Golf UK"))
check("eventkanaler är inte egna golfkanaler", ":Golf  02" not in golf, list(golf))
st2 = xmltvguide.fill(now=now + 60, client=Client(), opener=opener)
check("filen hämtas inte igen inom 12 timmar", len(calls) == 1 and st2["ok"], (len(calls), st2))


def bad(req, timeout=None):
    raise OSError("nätverket nere")


st3 = xmltvguide.fill(force=True, now=now, client=Client(), opener=bad)
check("fel sparas i statusen utan adress eller lösenord", not st3["ok"] and "nätverket" in st3["error"] and "p" != st3["error"], st3)

print("ALLT OK" if fails == 0 else f"{fails} FEL")
sys.exit(1 if fails else 0)
