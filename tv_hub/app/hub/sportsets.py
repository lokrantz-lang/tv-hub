"""Sportuppsättningar (Golf, Ridsport): vilka kanaler som sänder sporten, och sökord för sändningar och nya titlar.

Kanallistan bygger på vilka kanaler och tjänster som sänder sporten i Sverige 2025–2026 (research i november 2026):
  Golf: V Sport Golf / Viaplay Golf (DP World Tour, Ryder Cup, Solheim Cup, LPGA, majors), Eurosport 1 och 2 och
        Discovery+/Max/HBO Max (PGA Tour, PGA Championship), SVT (The Masters), Golf Channel och Sky Sports Golf om
        de finns i listan.
  Ridsport: TV4 Sportkanalen och TV4 Play Sport (världscupen i hoppning, mästerskap), SVT (världscupfinalerna,
        Göteborg Horse Show), Eurosport 2 och HBO Max (Global Champions Tour), Horse & Country, FEI TV.
Leverantörens egna kanalnamn avgör vad som finns: webbadminen visar bara det som hittas i din kanallista, och du
godkänner urvalet innan en grupp skapas.
"""
import json
import re

from . import catalog, db, extras, sports

SETS = {
    "Golf": {
        # Egna kanaler (namnet räcker): hamnar i gruppen
        "dedicated": [r"\bgolf\b", r"\bpga\b", r"\bv ?sport golf\b", r"\bviaplay golf\b"],
        "dedicated_exclude": [r"mini ?golf", r"disc ?golf", r"golf ?(tv )?shop", r"vw golf"],
        # Kanaler som sänder golf bland annat: läses av programguiden men läggs inte i gruppen
        "carriers": [r"\beurosport\b", r"\bv ?sport\b", r"\bviaplay\b", r"\btv ?4 ?(play )?sport", r"\bsvt ?[12]\b",
                     r"\bsky ?sports?\b", r"\bdiscovery\b", r"\bmax ?sport", r"\bhbo ?max\b"],
        "keywords": ["golf", "pga tour", "pga championship", "lpga", "dp world tour", "european tour", "ryder cup",
                     "solheim cup", "presidents cup", "liv golf", "ladies european tour", "korn ferry", "golf channel",
                     "open championship", "us open + golf", "the masters + golf", "golfen"],
        "exclude": ["minigolf", "discgolf", "vw golf"],
        "watch": ["golf"],
    },
    "Ridsport": {
        "dedicated": [r"\bhorse\b", r"\bequest", r"\bridsport\b", r"\bh ?[&o] ?c\b", r"\bhorse ?(&|and) ?country\b",
                      r"\bfei\b", r"\bhäst", r"\bhast"],
        "dedicated_exclude": [r"racing", r"trav", r"galopp", r"\bv ?[0-9]{2}\b", r"vinnare", r"horse ?power"],
        "carriers": [r"\btv ?4 ?(play )?sport", r"\btv ?4\b", r"\bsvt ?[12]\b", r"\beurosport ?[12]?\b", r"\bhbo ?max\b",
                     r"\bviaplay\b", r"\bv ?sport\b"],
        "keywords": ["ridsport", "hästhoppning", "hoppning", "banhoppning", "dressyr", "fälttävlan", "horse show",
                     "show jumping", "global champions", "equestrian", "eventing", "dressage", "nationernas cup",
                     "nations cup", "world cup jumping", "världscupen + hoppning", "longines", "göteborg horse show",
                     "stockholm horse show", "falsterbo horse show", "hästsport"],
        "exclude": ["trav", "galopp", "v75", "v86", "v64", "v65", "travsport", "horse racing"],
        "watch": ["ridsport", "dressyr", "hoppning"],
    },
}


def _norm(name):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9åäö&]+", " ", str(name or "").casefold())).strip()


def _matches(name, include, exclude):
    n = _norm(name)
    return any(re.search(p, n) for p in include) and not any(re.search(p, n) for p in exclude)


def _channels(set_name, key, exclude_key=None):
    cfg = SETS[set_name]
    include = cfg[key]
    exclude = cfg[exclude_key] if exclude_key else []
    rows = db.q("""SELECT i.item_id AS id, i.name, i.archive_days, COALESCE(NULLIF(p.display_name, ''), c.name) AS category
                   FROM items i LEFT JOIN categories c ON c.type = i.type AND c.category_id = i.category_id
                   LEFT JOIN category_prefs p ON p.type = i.type AND p.category_id = i.category_id
                   WHERE i.type = 'live' AND i.adult = 0 ORDER BY i.name""")
    out = []
    for r in rows:
        if _matches(r["name"], include, exclude) and not _adult_category(r["category"]):
            out.append({"id": r["id"], "name": r["name"], "category": r["category"],
                        "archive": int(r["archive_days"] or 0) > 0})
    return out


def _adult_category(name):
    return bool(re.search(r"\b(xxx|adult|18\+|porn|erotic)\b", str(name or "").casefold()))


def dedicated(set_name):
    return _channels(set_name, "dedicated", "dedicated_exclude")


def carriers(set_name):
    ded = {c["id"] for c in dedicated(set_name)}
    return [c for c in _channels(set_name, "carriers") if c["id"] not in ded]


def scan_extra_channels():
    """Kanaler som sportskanningen (programguiden) ska läsa utöver svenska kategorier: allt som sänder någon uppsättning."""
    ids = set()
    for name in SETS:
        ids.update(c["id"] for c in dedicated(name))
        ids.update(c["id"] for c in carriers(name))
    return ids


def _group_id(set_name):
    row = db.q1("SELECT id FROM groups WHERE name = ?", (set_name,))
    return row["id"] if row else None


def overview():
    out = []
    for name, cfg in SETS.items():
        gid = _group_id(name)
        members = {r["item_id"] for r in db.q("SELECT item_id FROM group_items WHERE group_id = ? AND type = 'live'", (gid,))} if gid else set()
        ded = dedicated(name)
        for c in ded:
            c["in_group"] = c["id"] in members
        watches = {w["query"].casefold() for w in extras.watches()}
        out.append({
            "name": name, "group_id": gid, "dedicated": ded, "carriers": carriers(name),
            "keywords": cfg["keywords"], "watch": cfg["watch"],
            "watching": [w for w in cfg["watch"] if w.casefold() in watches],
        })
    return out


def apply(set_name, ids, watches=True):
    """Skapar (eller fyller på) gruppen med de valda kanalerna, uppdaterar sökorden och sätter bevakningar på nya
    filmer och serier. Tar aldrig bort något."""
    if set_name not in SETS:
        raise LookupError("Okänd uppsättning.")
    allowed = {c["id"] for c in dedicated(set_name)} | {c["id"] for c in carriers(set_name)}
    chosen = [str(i) for i in (ids or []) if str(i) in allowed]
    gid = _group_id(set_name)
    if gid is None and chosen:
        gid = catalog.create_group(set_name)
    for cid in chosen:
        catalog.add_to_group(gid, "live", cid)
    merged = upgrade_interest(set_name)
    created = 0
    if watches:
        have = {(w["query"].casefold(), w["type"]) for w in extras.watches()}
        for word in SETS[set_name]["watch"]:
            for kind in ("vod", "series"):
                if (word.casefold(), kind) not in have:
                    extras.create_watch(word, kind)
                    created += 1
    sports.request_scan()
    return {"group_id": gid, "added": len(chosen), "keywords": merged, "watches": created}


def upgrade_interest(set_name):
    """Lägger till de nya sökorden i sportintresset med samma namn (befintliga sökord och egna ändringar behålls)."""
    cfg = SETS[set_name]
    row = db.q1("SELECT * FROM sport_interests WHERE lower(name) = ?", (set_name.casefold(),))
    if not row:
        sports.create_interest({"name": set_name, "kind": "sport", "keywords": cfg["keywords"], "exclude": cfg["exclude"],
                                "title_only": True})
        return len(cfg["keywords"])
    kw = json.loads(row["keywords"] or "[]")
    ex = json.loads(row["exclude"] or "[]")
    have = {k.casefold() for k in kw}
    new_kw = kw + [k for k in cfg["keywords"] if k.casefold() not in have]
    have_ex = {k.casefold() for k in ex}
    new_ex = ex + [k for k in cfg["exclude"] if k.casefold() not in have_ex]
    if new_kw != kw or new_ex != ex:
        db.x("UPDATE sport_interests SET keywords = ?, exclude = ? WHERE id = ?",
             (json.dumps(new_kw, ensure_ascii=False), json.dumps(new_ex, ensure_ascii=False), row["id"]))
        sports.rematch([row["id"]])
    return len(new_kw) - len(kw)


def ensure_keywords():
    """Vid start (en gång): de bättre sökorden läggs till i Golf och Ridsport om intressena finns."""
    if db.get_setting("sportsets_kw_v1"):
        return
    for name in SETS:
        if db.q1("SELECT 1 FROM sport_interests WHERE lower(name) = ?", (name.casefold(),)):
            upgrade_interest(name)
    db.set_setting("sportsets_kw_v1", "1")
