"""Enhetstest av kategorival: Inte i Alla, Sport-fliken, profilval och filmfilter. Kör: python3 tests/test_filters.py"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="tvhub-filters-")

from hub import backup, catalog, db, profiles  # noqa: E402

db.connect()
bad = []


def check(name, cond, info=None):
    print(("  ok   " if cond else "  FEL  ") + name + ("" if cond else f"  -> {info}"))
    if not cond:
        bad.append(name)


now = int(time.time())
for cid, name in (("1", "Premiere Cinemas [Multi-Sub]"), ("2", "N1 Sports Events Replays"), ("3", "Kids Movies")):
    db.x("INSERT INTO categories (type, category_id, name, sort) VALUES ('vod', ?, ?, ?)", (cid, name, int(cid)))


def item(i, name, cat, icon="http://x/p.png"):
    db.x("INSERT INTO items (type, item_id, name, name_norm, category_id, icon, first_seen, added) VALUES ('vod', ?, ?, ?, ?, ?, ?, ?)",
         (str(i), name, catalog.normalize(name), cat, icon, now, now))


item(1, "Dune (2024)", "1")
item(2, "Frost (2013)", "1", icon="")
item(3, "00.00 Oklahoma vs. Mississippi State 10.24", "2")
item(4, "Rugby final", "2")
item(5, "Bamse", "3")
item(6, "00.30 Lag A vs Lag B", "1")   # sportliknande titel i en vanlig kategori

scope = catalog.Scope(adult=0, hide_hidden=True)


def names(**params):
    items, _ = catalog.browse("vod", scope, params.pop("category", None), params, 0, 100)
    return {i["name"] for i in items}


check("alla syns från början", len(names()) == 6, names())
cats = {c["category_id"]: c for c in catalog.categories("vod", scope)}
check("sportkategorin föreslås", cats["2"]["suggest_sport"] and not cats["1"]["suggest_sport"] and not cats["3"]["suggest_sport"], cats["2"])

catalog.set_category_prefs("vod", [{"category_id": "2", "skip_all": True}])
check("Inte i Alla: borta ur Alla", names() == {"Dune (2024)", "Frost (2013)", "Bamse", "00.30 Lag A vs Lag B"}, names())
check("Inte i Alla: kvar i egen kategori", len(names(category="2")) == 2)
check("Inte i Alla: kvar i sök", "Rugby final" in names(q="rugby"), names(q="rugby"))
check("Inte i Alla: borta ur Nytt tillagt", "Rugby final" not in names(new_only="1", sort="added"))
check("Sport-fliken visar bara dem", names(skipped_only="1") == {"00.00 Oklahoma vs. Mississippi State 10.24", "Rugby final"}, names(skipped_only="1"))
cats = {c["category_id"]: c for c in catalog.categories("vod", scope)}
check("valet gör att förslaget försvinner", cats["2"]["skip_all"] and not cats["2"]["suggest_sport"])
check("sammanfattningen Nytt per dag utan sport", all(c["category_id"] != "2" for d in catalog.new_summary(scope) for c in d["categories"]))
catalog.set_category_prefs("vod", [{"category_id": "1", "hidden": False}])
check("andra val raderar inte skip_all", {c["category_id"]: c for c in catalog.categories("vod", scope)}["2"]["skip_all"])

# Filmfilter
catalog.save_browse_options({"hide_sport_titles": True})
check("sportreprisor döljs i Alla", "00.30 Lag A vs Lag B" not in names() and "Dune (2024)" in names(), names())
check("sportreprisor syns i kategorin", "00.30 Lag A vs Lag B" in names(category="1"))
check("sportreprisor syns i sök", "00.30 Lag A vs Lag B" in names(q="lag"))
catalog.save_browse_options({"hide_sport_titles": False, "new_requires_poster": True})
check("Nytt: bara med affisch", "Frost (2013)" not in names(new_only="1", sort="added") and "Dune (2024)" in names(new_only="1", sort="added"))
check("Alla påverkas inte av affischvalet", "Frost (2013)" in names())
catalog.save_browse_options({"new_requires_poster": False})

# Profilval
owner = profiles.owner_id()
kid = profiles.create({"name": "Barn"})["id"]
catalog.set_profile_category_prefs(kid, "vod", [{"category_id": "1", "hidden": True}])
kscope = catalog.Scope(adult=0, hide_hidden=True, profile_id=kid)
kcats = [c["category_id"] for c in catalog.categories("vod", kscope)]
check("profilen ser inte den dolda kategorin", "1" not in kcats and "3" in kcats, kcats)
items, _ = catalog.browse("vod", kscope, None, {}, 0, 100)
check("profilen ser inte titlarna i Alla", all(i["name"] not in ("Dune (2024)", "Frost (2013)") for i in items), [i["name"] for i in items])
ocats = [c["category_id"] for c in catalog.categories("vod", catalog.Scope(adult=0, hide_hidden=True, profile_id=owner))]
check("andra profiler påverkas inte", "1" in ocats, ocats)
catalog.set_profile_category_prefs(kid, "vod", [{"category_id": "3", "position": 0}, {"category_id": "2", "position": 1}])
kcats = [c["category_id"] for c in catalog.categories("vod", kscope)]
check("profilens egen ordning", kcats[:2] == ["3", "2"], kcats)
adm = {c["category_id"]: c for c in catalog.categories("vod", for_profile=kid)}
check("admin ser profilens val", adm["1"]["profile_hidden"] is True and adm["3"]["profile_hidden"] is False)
data = backup.export_all()
check("kopian har profilval och skip_all", any(p["profile"] == "Barn" for p in data["profile_category_prefs"])
      and any(c.get("skip_all") == 1 for c in data["category_prefs"]))
catalog.reset_profile_category_prefs(kid, "vod")
check("återställ tar bort profilens val", "1" in [c["category_id"] for c in catalog.categories("vod", kscope)])
catalog.set_profile_category_prefs(kid, "vod", [{"category_id": "1", "hidden": True}])
profiles.delete(kid)
check("profilval försvinner med profilen", db.q1("SELECT COUNT(*) AS n FROM profile_category_prefs")["n"] == 0)

print("\nALLT OK" if not bad else f"\n{len(bad)} FEL: {', '.join(bad)}")
sys.exit(1 if bad else 0)
