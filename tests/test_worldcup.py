"""Världscupen: kalender, matchning mot programguiden, status, knappar och kontroll (utan nätverk)."""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tvhub-wc-")

from hub import db, worldcup  # noqa: E402

db.connect()
fails = 0


def check(name, cond, info=None):
    global fails
    print(("  ok   " if cond else "  FEL  ") + name + ("" if cond else f"  -> {info}"))
    fails += 0 if cond else 1


def ts(day, clock):
    return worldcup._ts(day, clock)


def channel(cid, name, archive, epg):
    db.x("INSERT INTO items (type, item_id, name, name_norm, category_id, epg_id, archive_days) VALUES ('live', ?, ?, ?, 'c', ?, ?)",
         (cid, name, name.casefold(), epg, archive))


def program(epg, title, start, end, desc=""):
    db.x("INSERT INTO epg (epg_key, start, end, title, title_norm, description) VALUES (?,?,?,?,?,?)",
         (epg, start, end, title, title.casefold(), desc))


channel("1", "SE: TV4 HD", 7, "tv4.se")
channel("2", "SE: Sjuan HD", 0, "sjuan.se")
channel("3", "CCTV4", 0, "cctv4")
channel("4", "SE: TV4 Film", 7, "tv4film.se")

# kalender
legs = {leg["id"]: leg for leg in worldcup.LEGS}
check("13 etapper, final och SIHS", len([l for l in worldcup.LEGS if l["kind"] == "league"]) == 13 and "final" in legs and "sihs" in legs)
check("Madrid söndag 29 nov 15:00", worldcup.wc_time(legs["madrid"]) == ts("2026-11-29", "15:00"))
check("London 14:35 lokal tid är 15:35 svensk tid", worldcup.wc_time(legs["london"]) == ts("2026-12-20", "15:35"))
check("tid som inte är fastställd ger None", worldcup.wc_time(legs["leipzig"]) is None)
check("Oslo och Lyon har vinnare inlagda", legs["oslo"]["winner"].startswith("Max K") and legs["lyon"]["winner"].startswith("Julien"))

# matchning
m = worldcup._leg_match
check("Världscupen i hästhoppning matchar Verona", m(legs["verona"], "Världscupen i hästhoppning, Verona", ""))
check("hoppning + världscupen utan ort matchar", m(legs["madrid"], "Världscupen i hoppning", ""))
check("hoppning med ortnamn matchar", m(legs["madrid"], "Hoppning: Madrid", ""))
check("hoppning utan världscup eller ort matchar inte", not m(legs["madrid"], "Hoppning", "Lokal tävling"))
check("fotbollens världscup matchar inte", not m(legs["madrid"], "Världscupen i längdskidåkning", ""))
check("dressyr matchar inte", not m(legs["madrid"], "Världscupen i dressyr", "Kür"))
check("trav matchar inte", not m(legs["madrid"], "Travet: Världscupen", ""))
check("SIHS matchar", m(legs["sihs"], "Sweden International Horse Show", "Grand Prix"))
check("final kräver final eller Göteborg", m(legs["final"], "Världscupfinalen i hoppning", "") and m(legs["final"], "Världscupen i hoppning", "Göteborg") and not m(legs["final"], "Världscupen i hoppning", ""))

# Verona sändes på Sjuan (arkiv saknas), Madrid kommer på TV4 (arkiv finns)
verona = (ts("2026-11-08", "14:15"), ts("2026-11-08", "17:00"))
program("sjuan.se", "Världscupen i hästhoppning, Verona", verona[0], verona[1], "Direkt från Verona")
mad = (ts("2026-11-29", "14:45"), ts("2026-11-29", "17:15"))
program("tv4.se", "Världscupen i hoppning: Madrid", mad[0], mad[1])
program("tv4.se", "Världscupen i hoppning: Madrid, sammanfattning", mad[0] + 86400, mad[0] + 86400 + 1800)
program("tv4.se", "Nyheterna", mad[0] + 3 * 3600, mad[0] + 3 * 3600 + 1800)

# före Verona: sändningen finns som kommande
v = worldcup.overview(now=ts("2026-11-08", "10:00"))
by = {leg["id"]: leg for leg in v["legs"]}
check("Verona: kommande sändning på Sjuan", by["verona"]["events"] and by["verona"]["events"][0]["channel"] == "SE: Sjuan HD", by["verona"]["events"])
e = by["verona"]["events"][0]
check("kommande: knappen är Spela in med marginal", e["record"]["action"] == "record" and e["record"]["body"]["start"] == e["start"] - 120
      and e["record"]["body"]["end"] == e["end"] + 900, e["record"])
far = worldcup.overview(now=ts("2026-11-01", "10:00"))
fe = {leg["id"]: leg for leg in far["legs"]}["madrid"]["events"][0]
check("mer än två veckor bort: ingen inspelningsknapp utan förklaring", fe["record"]["action"] == "later" and fe["record"]["body"] is None, fe["record"])
check("Madrid hittas inte i Veronas fönster", by["madrid"]["events"] == [] or by["madrid"]["events"][0]["start"] > ts("2026-11-20", "00:00"))

# under sändningen
v = worldcup.overview(now=ts("2026-11-08", "15:00"))
e = {leg["id"]: leg for leg in v["legs"]}["verona"]["events"][0]
check("under sändningen: Titta nu och inspelning direkt", e["state"] == "now" and e["play_label"] == "Titta nu" and e["record"]["action"] == "record", e)
check("pågående sändning ger ett kommando", e["command"]["type"] == "live")

# efter sändningen på en kanal utan arkiv: finns inte längre, men har sparats
v = worldcup.overview(now=ts("2026-11-09", "10:00"))
vv = {leg["id"]: leg for leg in v["legs"]}["verona"]
check("utan arkiv: ingen spelbar sändning", vv["events"] == [] and vv["headline"].startswith("Sändes, men finns inte"), vv["headline"])
check("sändningen finns sparad i historiken", vv["seen"] and vv["seen"][0]["channel"] == "SE: Sjuan HD" and "Verona" in vv["seen"][0]["title"], vv["seen"])
check("Verona är klar", vv["state"] == "done")

# Madrid: huvudsändningen är den längsta, och en sammanfattning är en egen sändning
v = worldcup.overview(now=ts("2026-11-28", "09:00"))
mm = {leg["id"]: leg for leg in v["legs"]}["madrid"]
check("Madrid: två sändningar, bara en huvud", len(mm["events"]) == 2 and sum(1 for x in mm["events"] if x["main"]) == 1, [x["title"] for x in mm["events"]])
check("huvudsändningen är världscupklassen", [x for x in mm["events"] if x["main"]][0]["title"] == "Världscupen i hoppning: Madrid")
check("Nyheterna kommer inte med", not any("Nyheterna" in x["title"] for x in mm["events"]))
check("nästa etapp är Madrid", v["next"] == "madrid", v["next"])

# utan guidepost: en sändning enligt tablå på Sjuan (kanalen finns i listan)
v = worldcup.overview(now=ts("2026-11-12", "09:00"))
st = {leg["id"]: leg for leg in v["legs"]}["stuttgart"]
check("Stuttgart saknas i guiden men får en rad enligt tablå", len(st["events"]) == 1 and st["events"][0]["unconfirmed"] and st["events"][0]["channel"] == "SE: Sjuan HD", st["events"])
check("raden har rätt tid och en Spela in-knapp", st["events"][0]["start"] == ts("2026-11-15", "14:45") and st["events"][0]["record"]["action"] in ("record", "later"), st["events"][0])
check("rubriken säger att den inte är bekräftad", "ej bekräftad" in st["headline"] and st["level"] == "warn", st["headline"])
v = worldcup.overview(now=ts("2026-11-15", "15:30"))
st = {leg["id"]: leg for leg in v["legs"]}["stuttgart"]
check("under tablåtiden: Titta nu som live på Sjuan", st["events"][0]["state"] == "now" and st["events"][0]["command"]["type"] == "live", st["events"])
v = worldcup.overview(now=ts("2026-11-17", "10:00"))
st = {leg["id"]: leg for leg in v["legs"]}["stuttgart"]
check("efteråt utan arkiv på kanalen: ingen påhittad rad", st["events"] == [], st["events"])
v = worldcup.overview(now=ts("2026-11-28", "09:00"))
mm = {leg["id"]: leg for leg in v["legs"]}["madrid"]
check("finns sändningen i guiden dubbleras den inte av tablån", not any(e.get("unconfirmed") for e in mm["events"]), mm["events"])

# efter Madrid med arkiv: ladda ner
v = worldcup.overview(now=ts("2026-11-29", "21:00"))
mm = {leg["id"]: leg for leg in v["legs"]}["madrid"]
main = [x for x in mm["events"] if x["main"]][0]
check("i arkivet: Titta i efterhand och Ladda ner", main["state"] == "past" and main["play_label"] == "Titta i efterhand"
      and main["record"]["action"] == "download" and main["record"]["body"]["start"] == main["start"]
      and main["command"]["type"] == "catchup", main)
check("nästa etapp efter Madrid är A Coruña", v["next"] == "acoruna", v["next"])

# manuella resultat
worldcup.set_result("verona", "Daniel Deusser (GER)", "Otello")
v = worldcup.overview(now=ts("2026-11-29", "21:00"))
check("resultat kan läggas in", {leg["id"]: leg for leg in v["legs"]}["verona"]["winner"] == "Daniel Deusser (GER)")
try:
    worldcup.set_result("finnsinte", "x", "y")
    check("okänd etapp nekas", False)
except LookupError:
    check("okänd etapp nekas", True)

# kontroll
c = worldcup.check(now=ts("2026-11-28", "09:00"))
by = {i["label"]: i for i in c["items"]}
check("kontrollen hittar TV4 och inte CCTV4 eller TV4 Film", [x["name"] for x in by["TV4"]["channels"]] == ["SE: TV4 HD"], by["TV4"]["channels"])
check("TV4: arkiv och guide finns", by["TV4"]["level"] == "ok", by["TV4"])
check("Sjuan utan arkiv varnar", by["Sjuan"]["level"] == "warn" and "inget arkiv" in by["Sjuan"]["text"], by["Sjuan"])
check("saknad kanal som inte krävs är dämpad", by["Eurosport"]["level"] == "muted" and "Finns inte" in by["Eurosport"]["text"], by["Eurosport"])
db.x("DELETE FROM items WHERE item_id = '2'")
c = worldcup.check(now=ts("2026-11-28", "09:00"))
by = {i["label"]: i for i in c["items"]}
check("saknad Sjuan är ett fel", by["Sjuan"]["level"] == "bad", by["Sjuan"])

# intresse för notis och automatisk inspelning
worldcup.ensure_interest()
it = worldcup.interest()
check("intresset skapas en gång med notis", it and it["notify"] and "världscupen + hoppning" in it["keywords"])
worldcup.ensure_interest()
check("inga dubbletter av intresset", db.q1("SELECT COUNT(*) AS n FROM sport_interests WHERE name = ?", (worldcup.INTEREST_NAME,))["n"] == 1)
db.x("INSERT INTO devices (name, model, token_hash, created, last_seen) VALUES ('Box', 'Z', 'h', ?, ?)", (db.iso(), db.iso()))
dev_id = db.q1("SELECT id FROM devices")["id"]
r = worldcup.auto_record(dev_id)
check("automatisk inspelning kan slås på", r["record_device_id"] == dev_id and worldcup.settings_view()["record_device_id"] == dev_id, r)
r = worldcup.auto_record(None)
check("och av", r["record_device_id"] is None)

print("ALLT OK" if not fails else f"{fails} FEL")
sys.exit(1 if fails else 0)
