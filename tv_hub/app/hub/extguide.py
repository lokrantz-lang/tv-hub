"""Extern tablå: TV4:s egen öppna tablå (tv4.se/tabla) fyller i programguiden för TV4, Sjuan och Sportkanalen när Toucan saknar den.

Varför: Toucans programguide saknar ofta TV4-kanalerna, och då syns varken världscupen eller annan hoppning i TV Hub. TV4:s tablå
visar riktiga tider för TV4 och Sjuan, flera dagar framåt. Den läses här och läggs in i samma programguide (tabellen epg) under
*dina* kanaler (kanalen ur din Toucan-lista), så att allt annat fungerar som förut: Titta, Spela in, Ladda ner, notiser, automatisk inspelning.

Regler:
  - Bara för kanaler där Toucans guide saknas eller är kort (mindre än tre dagar framåt). Toucans egna rader skrivs aldrig över.
  - Högst en sida per dag och högst 15 dagar, med en kort paus mellan sidorna. Hämtas som mest var fjärde timme (vid programguidens skanning).
  - Sidan tolkas generöst (kanalbild, klockslag, rubrik, rad under rubriken). Ändrar TV4 sidan syns det i statusen (0 program), inte som felaktiga tider.
  - Privat bruk i hemmet. Inget sparas utöver tablån.
"""
import html as htmllib
import re
import time

from . import db, limits, sports
from . import VERSION

URL = "https://www.tv4.se/tabla/{date}"
DAYS = 15
PAUSE_S = 0.6
STATUS_KEY = "extguide_status"
OWNED_KEY = "extguide_keys"
MIN_AHEAD_S = 3 * 86400
LOGO_NAMES = {"tv4-logo": "TV4", "tv7-logo": "Sjuan", "tv4sport": "TV4 Sportkanalen"}

_SKIP = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.I | re.S)
_IMG = re.compile(r"<img\b[^>]*>", re.I | re.S)
_ATTR = lambda name: re.compile(name + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.I)  # noqa: E731
_ALT, _SRC = _ATTR("alt"), _ATTR("src")
_TIME = re.compile(r"^\D{0,3}(\d{1,2})[:.](\d{2})\b\s*(.*)$")


def _attr(rx, tag):
    m = rx.search(tag)
    return htmllib.unescape((m.group(1) or m.group(2) or "").strip()) if m else ""


def _lines(raw):
    """HTML -> rader: kanalbild som '![namn]', rubrik som '### text', listrad som '- text'."""
    text = _SKIP.sub(" ", raw)

    def img(m):
        tag = m.group(0)
        name = _attr(_ALT, tag)
        src = _attr(_SRC, tag).casefold()
        for key, label in LOGO_NAMES.items():
            if key in src.replace("%2f", "/"):
                name = name or label
                if not name.strip():
                    name = label
        return f"\n![{name}|{src}]\n" if (name or "logo" in src) else " "
    text = _IMG.sub(img, text)
    text = re.sub(r"<h[1-6]\b[^>]*>", "\n### ", text, flags=re.I)
    text = re.sub(r"</h[1-6]\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<li\b[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"</?(?:p|br|div|ul|ol|li|section|article|header|footer|tr|td|a)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmllib.unescape(text)
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n") if ln.strip()]


def _channel_from(line):
    m = re.match(r"^!\[(.*?)\|(.*?)\]$", line)
    if not m:
        return None
    name, src = m.group(1).strip(), m.group(2)
    if not name:
        for key, label in LOGO_NAMES.items():
            if key in src:
                return label
    # "TV4 logotyp" -> "TV4"
    return re.sub(r"\b(logo|logotyp|logotype)\b", "", name, flags=re.I).strip() or None


def parse(raw, day):
    """Tolkar en dagssida. day = 'ÅÅÅÅ-MM-DD'. Returnerar {kanalnamn: [(start, titel, beskrivning), ...]} med start som epok."""
    y, mo, d = (int(x) for x in day.split("-"))
    base = time.mktime((y, mo, d, 0, 0, 0, 0, 0, -1))
    channels, current, row = {}, None, None
    last_minutes = {}

    def flush():
        nonlocal row
        if row and current and row["title"]:
            channels.setdefault(current, []).append((row["start"], row["title"], " ".join(row["desc"])[:300]))
        row = None

    for line in _lines(raw):
        name = _channel_from(line)
        if name is not None:
            flush()
            current = name
            continue
        if current is None or line.startswith("![") or "se hela dagen" in line.casefold():
            continue
        body = line[2:].strip() if line.startswith("- ") else line
        if line.startswith("- ") or _TIME.match(body):
            m = _TIME.match(body)
            if m:
                flush()
                minutes = int(m.group(1)) * 60 + int(m.group(2))
                if not (0 <= int(m.group(1)) < 24 and 0 <= int(m.group(2)) < 60):
                    continue
                prev = last_minutes.get(current)
                days = last_minutes.get(("d", current), 0)
                if prev is not None and minutes < prev - 30:
                    days += 1  # efter midnatt: nästa dygn
                last_minutes[current], last_minutes[("d", current)] = minutes, days
                start = int(base + days * 86400 + minutes * 60)
                # klockslaget kan ha titeln på samma rad
                row = {"start": start, "title": m.group(3).strip(" -–"), "desc": []}
                continue
        if row is None:
            continue
        if line.startswith("### "):
            title = line[4:].strip()
            if not row["title"]:
                row["title"] = title
            elif title:
                row["desc"].append(title)
        elif row["title"] or True:
            if line.startswith("- "):
                line = line[2:]
            if len(line) > 1:
                row["desc"].append(line)
    flush()
    return {ch: sorted(rows) for ch, rows in channels.items()}


def to_programs(rows):
    """[(start, titel, beskrivning)] -> [(start, slut, titel, beskrivning)]; slut = nästa start."""
    out = []
    rows = sorted({r[0]: r for r in rows}.values())
    for i, (start, title, desc) in enumerate(rows):
        end = rows[i + 1][0] if i + 1 < len(rows) else start + 3600
        if end > start:
            out.append((start, end, title, desc))
    return out


def fetch(day):
    with limits.open_public(URL.format(date=day), {"User-Agent": f"TVHub/{VERSION} (privat hemmabruk)", "Accept-Language": "sv"}, 20) as resp:
        return resp.read(3_000_000).decode("utf-8", "replace")


def _targets():
    """Vilka kanaler som ska fyllas: {etikett: (nyckel i epg, kanal ur din lista, mönster)}."""
    from . import worldcup
    out = {}
    for label, _required, include, exclude, _why in worldcup.CHANNEL_WANTS:
        if label not in ("TV4", "Sjuan", "TV4 Sportkanalen"):
            continue
        found = worldcup._find_channels(include, exclude)
        if found:
            c = found[0]
            out[label] = {"key": sports._epg_key(c), "channel": c["name"], "archive": int(c["archive_days"] or 0) > 0,
                          "include": include, "exclude": exclude}
    return out


def _label_for(page_name, targets):
    n = sports.norm(page_name)
    n = re.sub(r"\bsport live\b|\bsport\b", "sportkanalen", n) if "tv4" in n and "sport" in n else n
    name = n.replace("tv 4", "tv4")
    for label, t in targets.items():
        if any(re.search(p, name) for p in t["include"]) and not any(re.search(p, name) for p in t["exclude"]):
            return label
    return None


def fill(fetcher=None, now=None, days=DAYS):
    """Hämtar tablån och lägger in den. Returnerar statusen (sparas också)."""
    now = int(now or time.time())
    fetcher = fetcher or fetch
    targets = _targets()
    status = {"at": now, "days_ok": 0, "days_empty": 0, "errors": [], "page_channels": [], "channels": {}, "targets": {
        k: v["channel"] for k, v in targets.items()}}
    if not targets:
        status["errors"].append("Hittade varken TV4, Sjuan eller Sportkanalen i din kanallista.")
        db.set_setting(STATUS_KEY, status)
        return status
    # bara kanaler där Toucans guide saknas eller är kort
    owned = set(db.get_setting(OWNED_KEY) or [])
    need = {}
    for label, t in targets.items():
        row = db.q1("SELECT COUNT(*) AS n, MAX(end) AS last FROM epg WHERE epg_key = ?", (t["key"],))
        own = t["key"] in owned
        if row["n"] and row["last"] and row["last"] > now + MIN_AHEAD_S and not own:
            status["channels"][label] = {"channel": t["channel"], "source": "toucan", "programs": row["n"]}
            continue
        need[label] = t
    if not need:
        status["note"] = "Toucans programguide räcker för alla kanalerna."
        db.set_setting(STATUS_KEY, status)
        return status
    collected = {label: [] for label in need}
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    y, mo, d = (int(x) for x in today.split("-"))
    seen_names = set()
    for i in range(days):
        day = time.strftime("%Y-%m-%d", time.localtime(time.mktime((y, mo, d + i, 12, 0, 0, 0, 0, -1))))
        try:
            parsed = parse(fetcher(day), day)
        except Exception as exc:  # noqa: BLE001
            status["errors"].append(f"{day}: {type(exc).__name__}: {str(exc)[:80]}")
            if len(status["errors"]) >= 3 and status["days_ok"] == 0:
                break  # ingen mening att fortsätta
            time.sleep(PAUSE_S)
            continue
        if not parsed:
            status["days_empty"] += 1
        else:
            status["days_ok"] += 1
        for page_name, rows in parsed.items():
            seen_names.add(page_name)
            label = _label_for(page_name, need)
            if label:
                collected[label].extend(rows)
        if i + 1 < days:
            time.sleep(PAUSE_S)
    status["page_channels"] = sorted(seen_names)[:12]
    inserted = 0
    for label, rows in collected.items():
        t = need[label]
        programs = to_programs(rows)
        status["channels"][label] = {"channel": t["channel"], "source": "tv4.se", "programs": len(programs),
                                     "first": programs[0][0] if programs else None, "last": programs[-1][1] if programs else None}
        if not programs:
            continue
        with db.tx() as conn:
            if t["key"] in owned:
                conn.execute("DELETE FROM epg WHERE epg_key = ? AND start >= ?", (t["key"], now - 3600))
            conn.executemany(
                "INSERT OR IGNORE INTO epg (epg_key, start, end, title, title_norm, description, archive) VALUES (?,?,?,?,?,?,?)",
                [(t["key"], s, e, title[:200], sports.norm(title)[:200], (desc or "")[:400], 1 if t["archive"] else 0)
                 for s, e, title, desc in programs if e > now - 3600])
        owned.add(t["key"])
        inserted += len(programs)
    db.set_setting(OWNED_KEY, sorted(owned))
    status["inserted"] = inserted
    db.set_setting(STATUS_KEY, status)
    return status


def status():
    return db.get_setting(STATUS_KEY) or None


def run_now():
    """Hämtar, och räknar om träffar och inspelningar. För knappen i webbadminen."""
    st = fill()
    try:
        sports.rematch()
        sports.plan_recordings()
    except Exception as exc:  # noqa: BLE001
        print(f"[fel] extern tablå: {exc}", flush=True)
    return st
