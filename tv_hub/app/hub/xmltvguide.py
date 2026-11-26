"""Toucans samlade guidefil (XMLTV, xmltv.php) som komplement till guiden kanal för kanal.

Genomsökningen frågar en kanal i taget (get_simple_data_table). För många kanaler, till exempel V Sport Golf och
Golf Channel US, svarar Toucan tomt där. Den samlade guidefilen kan ha dem. Filen hämtas högst var tolfte timme
(eller med knappen), sparas på disken och läses efter varje genomsökning. Den fyller bara kanaler som saknar
program; Toucans svar per kanal och tablån från TV4.se skrivs aldrig över.
"""
import gzip
import os
import shutil
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from calendar import timegm

from . import db, portals, sports
from .config import CFG

STATUS_KEY = "xmltv_status"
MAX_AGE_S = 12 * 3600
MAX_BYTES = 1024 * 1024 * 1024  # 1 GB: större fil än så avbryts
CHUNK = 256 * 1024


def _path():
    return os.path.join(CFG["data_dir"], "xmltv.xml")


def status():
    return db.get_setting(STATUS_KEY) or None


def _ts(value):
    """'20261126143000 +0100' -> unix-tid. None om formatet är fel."""
    value = (value or "").strip()
    if len(value) < 14 or not value[:14].isdigit():
        return None
    try:
        base = timegm(time.strptime(value[:14], "%Y%m%d%H%M%S"))
    except ValueError:
        return None
    off = value[14:].strip()
    if len(off) == 5 and off[0] in "+-" and off[1:].isdigit():
        secs = int(off[1:3]) * 3600 + int(off[3:5]) * 60
        base -= secs if off[0] == "+" else -secs
    return base


def download(client, path=None, opener=None):
    """Hämtar xmltv.php till en fil (packas upp om den är gzip). Returnerar antal byte."""
    path = path or _path()
    query = urllib.parse.urlencode({"username": client.username, "password": client.password})
    url = f"{client.base}/xmltv.php?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": CFG["user_agent"], "Accept-Encoding": "gzip"})
    tmp = path + ".part"
    total = 0
    with (opener or urllib.request.urlopen)(req, timeout=120) as res, open(tmp, "wb") as fh:
        while True:
            chunk = res.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("Guidefilen är större än 1 GB.")
            fh.write(chunk)
    with open(tmp, "rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    if gz:
        with gzip.open(tmp, "rb") as src, open(path, "wb") as dst:
            shutil.copyfileobj(src, dst, CHUNK)
        os.remove(tmp)
    else:
        os.replace(tmp, path)
    return total


def _sport_channels():
    """Golf- och ridsportkanalerna (egna kanaler, utan eventkanaler) som har ett guide-id."""
    from . import sportsets
    out, seen = [], set()
    for set_name in sportsets.SETS:
        for c in sportsets.dedicated(set_name):
            row = db.q1("SELECT epg_id FROM items WHERE type = 'live' AND item_id = ?", (c["id"],))
            key = ((row and row["epg_id"]) or "").strip().casefold()
            if key and (set_name, key) not in seen:
                seen.add((set_name, key))
                out.append({"set": set_name, "name": c["name"], "key": key})
    return out


def parse(path, wanted, lo, hi, with_ids=False):
    """Läser programmen för de önskade kanalerna (EPG-id, gemener) inom tidsfönstret.
    Returnerar ({nyckel: [(start, slut, titel, beskrivning)]}, antal kanaler i filen, antal program i filen)
    och med with_ids också mängden kanal-id (gemener) som finns i filen."""
    out = {}
    ids = set()
    channels = programmes = 0
    for _event, el in ET.iterparse(path, events=("end",)):
        tag = el.tag
        if tag == "channel":
            channels += 1
            if with_ids:
                ids.add((el.get("id") or "").strip().casefold())
            el.clear()
        elif tag == "programme":
            programmes += 1
            key = (el.get("channel") or "").strip().casefold()
            if key in wanted:
                start, end = _ts(el.get("start")), _ts(el.get("stop"))
                if start and end and end > lo and start < hi and end > start:
                    title = (el.findtext("title") or "").strip()
                    if title:
                        out.setdefault(key, []).append((start, end, title, (el.findtext("desc") or "").strip()))
            if with_ids:
                ids.add((el.get("channel") or "").strip().casefold())
            el.clear()
    if with_ids:
        return out, channels, programmes, ids
    return out, channels, programmes


def fill(force=False, now=None, client=None, opener=None):
    """Efter genomsökningen: fyll kanaler utan program från guidefilen. Sparar och returnerar statusen."""
    now = int(now or time.time())
    st = status() or {}
    path = _path()
    try:
        stale = force or not os.path.isfile(path) or now - int(st.get("downloaded") or 0) > MAX_AGE_S
        if stale:
            if client is None:
                portal = portals.best_portal()
                if portal is None:
                    raise RuntimeError("Ingen portal fungerar just nu.")
                client = portals.client(portal)
            st["bytes"] = download(client, path, opener)
            st["downloaded"] = now
        keys = {k for k, _ in sports.scan_channels() if not k.startswith("ch:")}
        # Golf- och ridsportkanalerna tas alltid med, oavsett genomsökningens lista och kanalval.
        sport = _sport_channels()
        keys |= {c["key"] for c in sport}
        have = {r["epg_key"] for r in db.q("SELECT DISTINCT epg_key FROM epg WHERE end > ?", (now,))}
        wanted = keys - have
        found, channels, programmes, in_file = parse(path, wanted | {c["key"] for c in sport}, now - sports.KEEP_BACK_S,
                                                     now + sports.KEEP_AHEAD_S, with_ids=True)
        found = {k: v for k, v in found.items() if k in wanted}
        archive = {}
        for r in db.q("SELECT epg_id, MAX(archive_days) AS a FROM items WHERE type = 'live' GROUP BY lower(epg_id)"):
            if r["epg_id"]:
                archive[r["epg_id"].strip().casefold()] = int(r["a"] or 0) > 0
        inserted = 0
        with db.tx() as conn:
            for key, rows in found.items():
                conn.executemany(
                    "INSERT OR IGNORE INTO epg (epg_key, start, end, title, title_norm, description, archive) VALUES (?,?,?,?,?,?,?)",
                    [(key, s, e, t[:200], sports.norm(t)[:200], (d or "")[:400], 1 if archive.get(key) else 0) for s, e, t, d in rows])
                inserted += len(rows)
        names = {}
        for key in sorted(found, key=lambda k: -len(found[k]))[:40]:
            r = db.q1("SELECT name FROM items WHERE type = 'live' AND lower(epg_id) = ? ORDER BY length(name) LIMIT 1", (key,))
            names[r["name"] if r else key] = len(found[key])
        # Svaret per golf- och hästkanal: finns den i filen, och hur många program har den nu.
        per = []
        for c in sport:
            n = db.q1("SELECT COUNT(*) AS n FROM epg WHERE epg_key = ? AND end > ?", (c["key"], now))["n"]
            per.append({"set": c["set"], "name": c["name"], "key": c["key"], "in_file": c["key"] in in_file,
                        "programs": n, "from_file": len(found.get(c["key"], []))})
        per.sort(key=lambda x: (x["set"], not x["programs"], x["name"].casefold()))
        st.update(at=now, ok=True, error=None, channels_in_file=channels, programs_in_file=programmes,
                  missing_before=len(wanted), filled=len(found), inserted=inserted, examples=names, sport=per)
    except Exception as exc:  # noqa: BLE001
        st.update(at=now, ok=False, error=f"{type(exc).__name__}: {str(exc)[:160]}")
    db.set_setting(STATUS_KEY, st)
    return st


def run_now():
    """Knappen i webbadminen: hämta filen på nytt och räkna om träffar."""
    st = fill(force=True)
    try:
        sports.rematch()
        sports.plan_recordings()
    except Exception as exc:  # noqa: BLE001
        print(f"[fel] guidefil: {exc}", flush=True)
    return st
