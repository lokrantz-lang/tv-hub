#!/usr/bin/env python3
"""End-to-end-test: startar en simulerad Xtream-server och TV Hub, och
går igenom hela flödet via HTTP. Kör: python3 tests/test_e2e.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "tv_hub", "app", "main.py")
FAKE = os.path.join(ROOT, "tests", "fake_xtream.py")
FAKE_PORT, ADMIN, DEVICE = 18091, 18080, 18099
A = f"http://127.0.0.1:{ADMIN}"
D = f"http://127.0.0.1:{DEVICE}"
FAILS = []


def call(method, url, body=None, token=None, raw=False, mode=None, history=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if mode:
        req.add_header("X-TVHub-Mode", mode)
    if history:
        req.add_header("X-TVHub-History", history)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            payload = res.read()
            return res.status, (payload if raw else json.loads(payload or b"null"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def check(name, condition, info=None):
    print(("  ok   " if condition else "  FEL  ") + name + ("" if condition else f"  -> {info}"))
    if not condition:
        FAILS.append(name)


def wait_for(fn, timeout=20, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        result = fn()
        if result:
            return result
        time.sleep(step)
    return None


def fake_mode(mode, **extra):
    query = "&".join([f"mode={mode}"] + [f"{k}={v}" for k, v in extra.items()])
    urllib.request.urlopen(f"http://127.0.0.1:{FAKE_PORT}/__mode?{query}").read()


def main():
    data_dir = tempfile.mkdtemp(prefix="tvhub-test-")
    env = dict(os.environ, DATA_DIR=data_dir, ADMIN_PORT=str(ADMIN), DEVICE_PORT=str(DEVICE),
               CHECK_INTERVAL="2", NET_CHECK_INTERVAL="2", TIMEOUT="3", SYNC_TIMEOUT="2",
               REFERENCE_HOSTS=f"127.0.0.1:{FAKE_PORT}", OPTIONS_PATH="/nonexistent",
               ADMIN_ALLOWED_IPS="127.0.0.1", NO_PROXY="*", no_proxy="*",
               INFO_START_DELAY="1", INFO_PAUSE="0.05", APP_RELEASE_BASE=f"http://127.0.0.1:{FAKE_PORT}/release")
    env.pop("HTTPS_PROXY", None); env.pop("https_proxy", None)
    fake = subprocess.Popen([sys.executable, FAKE, str(FAKE_PORT)])
    hub = subprocess.Popen([sys.executable, APP], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        wait_for(lambda: _up(f"{D}/device/ping"))
        print("Portaler")
        s, dead = call("POST", f"{A}/api/portals", {"name": "Död", "url": "http://127.0.0.1:18092/",
                                                    "username": "testuser", "password": "testpass", "priority": 1})
        check("skapa portal som inte svarar", s == 201, dead)
        s, good = call("POST", f"{A}/api/portals", {"name": "Toucan 1", "url": f"http://127.0.0.1:{FAKE_PORT}/player_api.php",
                                                    "username": "testuser", "password": "testpass", "priority": 2})
        check("skapa fungerande portal", s == 201 and good["ok"] is True, good)
        check("URL normaliseras", good["url"] == f"http://127.0.0.1:{FAKE_PORT}", good["url"])
        check("lösenord returneras aldrig", "password" not in good, good.keys())
        s, bad = call("POST", f"{A}/api/portals", {"name": "x", "url": "ftp://x", "username": "a", "password": "b"})
        check("ogiltig URL avvisas", s == 400, bad)
        s, ov = call("GET", f"{A}/api/overview")
        check("fungerande portal blir aktiv trots lägre prioritet", ov["active_portal_id"] == good["id"], ov["active_portal_id"])

        print("Katalog")
        call("POST", f"{A}/api/catalog/sync")
        run = wait_for(lambda: _sync_done())
        check("synk klar", run and run["status"] == "ok", run)
        s, ov = call("GET", f"{A}/api/overview")
        check("räknare", ov["catalog"]["live"]["items"] == 8 and ov["catalog"]["vod"]["items"] == 6
              and ov["catalog"]["series"]["items"] == 1, ov["catalog"])
        s, res = call("GET", f"{A}/api/library/search?q=svt")
        check("sök 'svt' ger 2 kanaler", len(res) == 2 and all(r["type"] == "live" for r in res), res)
        s, res = call("GET", f"{A}/api/library/search?q=J%C3%96NS")
        check("sök med å/ä/ö och versaler", len(res) == 1 and "Jönssonligan" in res[0]["name"], res)
        s, res = call("GET", f"{A}/api/library/search?q=dune%20two")
        check("sök på flera ord", len(res) == 1, res)
        s, cats = call("GET", f"{A}/api/library/categories?type=live")
        check("kategorier med antal", cats[0]["count"] == 4, cats)
        s, ser = call("GET", f"{A}/api/library/series/900")
        check("serie med säsonger", len(ser["seasons"]) == 2 and ser["seasons"][0]["episodes"][0]["id"] == "9001", ser)

        print("Favoriter och grupper")
        call("POST", f"{A}/api/favorites", {"type": "live", "id": "101", "on": True})
        call("POST", f"{A}/api/favorites", {"type": "vod", "id": "501", "on": True})
        s, favs = call("GET", f"{A}/api/favorites")
        check("två favoriter", len(favs) == 2 and favs[0]["type"] == "live", favs)
        s, g = call("POST", f"{A}/api/groups", {"name": "Barnen"})
        check("skapa grupp", s == 201, g)
        s, dup = call("POST", f"{A}/api/groups", {"name": "Barnen"})
        check("dubblettgrupp nekas", s == 409, dup)
        call("POST", f"{A}/api/groups/{g['id']}/items", {"type": "vod", "id": "505"})
        call("POST", f"{A}/api/groups/{g['id']}/items", {"type": "vod", "id": "503"})
        call("PUT", f"{A}/api/groups/{g['id']}", {"order": [{"type": "vod", "id": "503"}, {"type": "vod", "id": "505"}]})
        s, groups = call("GET", f"{A}/api/groups")
        check("grupp med sorterade objekt", [i["id"] for i in groups[0]["items"]] == ["503", "505"], groups)

        print("Parning")
        s, res = call("GET", f"{D}/device/home")
        check("utan nyckel nekas", s == 401, res)
        s, code = call("POST", f"{A}/api/devices/pair-code")
        s, bad = call("POST", f"{D}/device/pair", {"code": "000000x"})
        check("fel kod nekas", s == 403, bad)
        s, pa = call("POST", f"{D}/device/pair", {"code": code["code"], "name": "Z10 Pro", "model": "Z10"})
        check("para Z10", s == 200 and pa["token"], pa)
        s, again = call("POST", f"{D}/device/pair", {"code": code["code"], "name": "x"})
        check("kod kan bara användas en gång", s == 403, again)
        s, code2 = call("POST", f"{A}/api/devices/pair-code")
        s, pb = call("POST", f"{D}/device/pair", {"code": code2["code"], "name": "Alpha", "model": "Z Alpha"})
        ta, tb = pa["token"], pb["token"]
        s, home = call("GET", f"{D}/device/home", token=ta)
        check("hemskärm", s == 200 and len(home["favorites"]) == 2 and home["groups"][0]["name"] == "Barnen", home.keys())
        s, ep = call("GET", f"{D}/device/epg/101", token=ta)
        check("EPG avkodas", ep and ep[0]["title"] == "Rapport", ep)
        s, nb = call("GET", f"{D}/device/neighbours/101", token=ta)
        check("kanal upp/ner", nb == {"prev": "104", "next": "102"}, nb)

        print("Dolt vuxenläge")
        check("admin ser antal vuxenobjekt", ov["catalog"].get("adult_items") == 2, ov["catalog"])
        call("POST", f"{A}/api/favorites", {"type": "live", "id": "190", "on": True})
        s, hn = call("GET", f"{D}/device/home", token=ta)
        live_cats = [c["name"] for c in hn["categories"]["live"]]
        check("vanligt läge: ingen vuxenkategori", "XXX | For Adults" not in live_cats and len(live_cats) == 2, live_cats)
        check("vanligt läge: räknar bort vuxeninnehåll", hn["counts"]["live"]["items"] == 7 and hn["counts"]["vod"]["items"] == 5, hn["counts"])
        check("vanligt läge: vuxenfavorit dold", all(f["id"] != "190" for f in hn["favorites"]), [f["id"] for f in hn["favorites"]])
        vod_cats_normal = [c["name"] for c in hn["categories"]["vod"]]
        check("vanligt läge: vuxenkategori för filmer dold", "18+ For Adults" not in vod_cats_normal, vod_cats_normal)
        s, res = call("GET", f"{D}/device/search?q=hot", token=ta)
        check("vanligt läge: sök hittar inte vuxenkanal", res == [], res)
        s, ha = call("GET", f"{D}/device/home", token=ta, mode="adult", history="show")
        live_cats = [c["name"] for c in ha["categories"]["live"]]
        vod_cats = [c["name"] for c in ha["categories"]["vod"]]
        check("vuxenläge: bara vuxenkategorin", live_cats == ["XXX | For Adults"], live_cats)
        check("vuxenläge: bara vuxenkategorin för filmer", vod_cats == ["18+ For Adults"], vod_cats)
        check("vuxenläge: bara vuxenfavoriter", [f["id"] for f in ha["favorites"]] == ["190"], ha["favorites"])
        check("vuxenläge: inga vanliga grupper", ha["groups"] == [], ha["groups"])
        s, items = call("GET", f"{D}/device/items?type=vod&category_id=12", token=ta, mode="adult")
        check("vuxenläge: vuxenfilmen i sin kategori", [i["id"] for i in items] == ["599"], items)
        s, hp = call("GET", f"{D}/device/home", token=ta, mode="adult")
        check("vuxenläge utan historik: favoriter dolda", hp["favorites"] == [] and hp["history"] is False, hp["favorites"])
        check("vuxenläge med historik: favoriter syns", ha["history"] is True, ha.get("history"))
        s, hn2 = call("GET", f"{D}/device/home", token=ta, history="hide")
        check("vanligt läge påverkas inte av historikvalet", hn2["history"] is True, hn2.get("history"))
        s, res = call("GET", f"{D}/device/search?q=hot", token=ta, mode="adult")
        check("vuxenläge: sök hittar vuxenkanal", [r["id"] for r in res] == ["190"], res)
        s, res = call("GET", f"{D}/device/search?q=svt", token=ta, mode="adult")
        check("vuxenläge: sök hittar inte vanliga kanaler", res == [], res)
        s, blocked = call("POST", f"{D}/device/play", {"type": "live", "id": "190"}, token=ta)
        check("vanligt läge: kan inte starta vuxenkanal", blocked["status"] == "error", blocked)
        s, blocked = call("POST", f"{D}/device/play", {"type": "live", "id": "101"}, token=ta, mode="adult")
        check("vuxenläge: kan inte starta vanlig kanal", blocked["status"] == "error", blocked)
        call("POST", f"{A}/api/favorites", {"type": "live", "id": "190", "on": False})

        print("Uppspelning och anslutningsvakt")
        s, play_a = call("POST", f"{D}/device/play", {"type": "live", "id": "101"}, token=ta)
        check("Z10 startar", play_a["status"] == "ok" and play_a["url"].endswith("/live/testuser/testpass/101.ts"), play_a)
        s, play_b = call("POST", f"{D}/device/play", {"type": "vod", "id": "502"}, token=tb)
        check("Alpha får konflikt", play_b["status"] == "conflict" and play_b["conflict"]["device"] == "Z10 Pro", play_b)
        s, play_b = call("POST", f"{D}/device/play", {"type": "vod", "id": "502", "force": True}, token=tb)
        check("Alpha väntar vid övertagande", play_b["status"] == "waiting", play_b)
        s, hb = call("POST", f"{D}/device/heartbeat", {"session_id": play_a["session_id"], "metrics": {}}, token=ta)
        check("Z10 ombeds stoppa", hb["stop"] is True and hb["reason"] == "takeover", hb)
        call("POST", f"{D}/device/stop", {"session_id": play_a["session_id"], "reason": "takeover"}, token=ta)
        s, play_b = call("POST", f"{D}/device/play", {"type": "vod", "id": "502", "force": True}, token=tb)
        check("Alpha startar efter att Z10 stoppat", play_b["status"] == "ok" and play_b["url"].endswith("/movie/testuser/testpass/502.mkv"), play_b)
        s, play_b2 = call("POST", f"{D}/device/play", {"type": "live", "id": "102"}, token=tb)
        check("kanalbyte på samma box ger ingen konflikt", play_b2["status"] == "ok", play_b2)
        s, hb_old = call("POST", f"{D}/device/heartbeat", {"session_id": play_b["session_id"]}, token=tb)
        check("gammal session stoppar inte nya", hb_old["stop"] is False, hb_old)
        s, ep_play = call("POST", f"{D}/device/play", {"type": "episode", "id": "9001", "ext": "mp4", "name": "Bonusfamiljen S01E01"}, token=tb)
        check("avsnitt", ep_play["status"] == "ok" and "/series/testuser/testpass/9001.mp4" in ep_play["url"], ep_play)
        s, inj = call("POST", f"{D}/device/play", {"type": "live", "id": "../../etc"}, token=tb)
        check("ogiltigt id avvisas", inj["status"] == "error", inj)
        s, fo = call("POST", f"{D}/device/play", {"type": "live", "id": "101", "exclude_portals": [good["id"]]}, token=tb)
        check("ingen reserv: tydligt fel", fo["status"] == "error", fo)

        print("Diagnostik")
        s, raw = call("GET", f"{D}/device/speedtest?mb=2", token=tb, raw=True)
        check("hastighetstest levererar rätt mängd", len(raw) == 2 * 1024 * 1024, len(raw))
        call("POST", f"{D}/device/report", {"kind": "speedtest", "detail": {"mbps": 12.5}}, token=tb)
        call("POST", f"{D}/device/report", {"kind": "stall", "session_id": ep_play["session_id"],
                                            "detail": {"bandwidth_bps": 8e6, "bitrate_bps": 6e6, "ms": 2300}}, token=tb)
        s, diag = call("GET", f"{A}/api/diagnostics")
        stall = [f for f in diag["diagnosis"]["findings"] if "stannar upp" in f["title"]]
        check("hack klassas som hemnätet (12,5 Mbit/s)", stall and stall[0]["area"] == "lan", stall)
        s, cap = call("POST", f"{A}/api/diagnostics/capacity-test", {"type": "vod"})
        check("kapacitetstest nekas när en box tittar", cap["ok"] is False and "tittar" in cap["error"], cap)
        call("POST", f"{D}/device/stop", {"session_id": ep_play["session_id"]}, token=tb)
        s, cap = call("POST", f"{A}/api/diagnostics/capacity-test", {"type": "vod", "seconds": 3})
        check("kapacitetstest mäter", cap.get("ok") and cap["mbps"] > 0, cap)

        fake_mode("ok", active_cons=1)
        call("POST", f"{A}/api/portals/{good['id']}/check")
        s, ov = call("GET", f"{A}/api/overview")
        foreign = [f for f in ov["diagnosis"]["findings"] if "inte kommer från TV Hub" in f["title"]]
        check("varnar för främmande anslutning (t.ex. MyTVOnline)", foreign, [f["title"] for f in ov["diagnosis"]["findings"]])
        fake_mode("ok", active_cons=0)

        fake_mode("vod_timeout")
        call("POST", f"{A}/api/catalog/sync")
        time.sleep(1)
        run = wait_for(lambda: _sync_done(), timeout=30)
        check("synk delvis när VOD inte svarar", run and run["status"] == "partial", run and run["status"])
        s, ov = call("GET", f"{A}/api/overview")
        cat = [f for f in ov["diagnosis"]["findings"] if f["area"] == "catalog"]
        check("förklarar varför VOD-listan inte uppdaterades",
              cat and "katalogtjänst" in cat[0]["detail"] and any("svarade inte i tid" in e for e in cat[0]["evidence"]), cat)
        check("gamla VOD-listan behålls", ov["catalog"]["vod"]["items"] == 6, ov["catalog"])
        fake_mode("vod_empty")
        call("POST", f"{A}/api/catalog/sync")
        time.sleep(1)
        wait_for(lambda: _sync_done(), timeout=30)
        s, ov = call("GET", f"{A}/api/overview")
        check("tom lista skriver inte över", ov["catalog"]["vod"]["items"] == 6, ov["catalog"])

        fake_mode("auth_fail")
        for _ in range(2):
            call("POST", f"{A}/api/portals/{good['id']}/check")
            call("POST", f"{A}/api/portals/{dead['id']}/check")
        s, ov = call("GET", f"{A}/api/overview")
        top = ov["diagnosis"]["findings"][0]
        check("alla portaler nere: källan pekas ut", top["area"] == "source" and top["level"] == "bad"
              and "inloggningen nekades" in top["detail"], top)
        s, summ = call("GET", f"{D}/device/summary", token=tb)
        check("sammanfattning för Home Assistant", summ["level"] == "bad" and summ["portals_total"] == 2, summ)
        fake_mode("ok")

        version_12(ta, tb, pa, pb, good)
        version_13(ta, tb, pa, pb)
        library_14()
        version_15(ta, tb, pa, pb)

        s, res = call("GET", f"{A}/", raw=True)
        check("webbadmin serveras", s == 200, s)
        s, res = call("GET", f"{D}/api/overview")
        check("admin-API nås inte via box-porten", s == 404, s)
        s, rm = call("DELETE", f"{A}/api/devices/{pb['device_id']}")
        s, res = call("GET", f"{D}/device/home", token=tb)
        check("borttagen box nekas", s == 401, s)
    finally:
        hub.terminate()
        fake.terminate()
        try:
            out, _ = hub.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            hub.kill()
            out = ""
        shutil.rmtree(data_dir, ignore_errors=True)
        errors = [line for line in (out or "").splitlines() if "[fel]" in line]
        if errors:
            print("Serverlogg med fel:\n  " + "\n  ".join(errors[:20]))
    print(f"\n{'ALLT OK' if not FAILS else f'{len(FAILS)} FEL: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


def version_12(ta, tb, pa, pb, good):
    print("Version 1.2: metadata, sortering och filter")
    wait_for(lambda: call("GET", f"{A}/api/portals")[1][0]["ok"] or any(p["ok"] for p in call("GET", f"{A}/api/portals")[1]), 15)
    s, det = call("GET", f"{D}/device/vod/501", token=ta)
    check("detaljsida hämtar filminfo direkt", s == 200 and det.get("duration_min") == 166
          and "Science" in (det.get("genre") or "") and det.get("plot"), det)
    done = wait_for(lambda: call("GET", f"{A}/api/overview")[1]["info"]["remaining"] == 0, 20)
    s, ov = call("GET", f"{A}/api/overview")
    check("bakgrundshämtning av filminfo klar", done and ov["info"]["done"] >= 5, ov["info"])
    s, b = call("GET", f"{D}/device/browse?type=vod&sort=duration", token=ta)
    check("sortera på längd (kortast först)", [i["id"] for i in b["items"]][:2] == ["505", "503"], [(i["id"], i.get("duration_min")) for i in b["items"]])
    s, b = call("GET", f"{D}/device/browse?type=vod&max_duration=120", token=ta)
    check("filter: kortare än 2 h", sorted(i["id"] for i in b["items"]) == ["503", "505"] and b["total"] == 2, b)
    s, b = call("GET", f"{D}/device/browse?type=vod&min_year=2020&sort=year", token=ta)
    check("filter: från 2020, sorterat på år", [i["id"] for i in b["items"]] == ["501", "502"], [(i["id"], i.get("year")) for i in b["items"]])
    s, b = call("GET", f"{D}/device/browse?type=vod&genre=Drama", token=ta)
    check("filter: genre", sorted(i["id"] for i in b["items"]) == ["502", "504"], b["items"])
    s, b = call("GET", f"{D}/device/browse?type=vod&min_rating=7&max_duration=200&min_year=1980&sort=rating", token=ta)
    check("kombinerade filter", b["total"] >= 1 and all(i["rating_num"] >= 7 for i in b["items"]), b)
    s, g = call("GET", f"{D}/device/genres?type=vod", token=ta)
    check("genrelista utan vuxengenre", "Drama" in [x["name"] for x in g] and "Vuxen" not in [x["name"] for x in g], g)
    s, b = call("GET", f"{D}/device/browse?type=series", token=ta)
    check("serie: år och längd från serielistan", b["items"][0].get("year") == 2017 and b["items"][0].get("duration_min") == 45, b["items"])

    print("Dolda kategorier")
    call("POST", f"{A}/api/categories/prefs", {"type": "live", "changes": [{"category_id": "2", "hidden": True},
                                                                           {"category_id": "1", "position": 0}]})
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("dold kategori syns inte i boxen", [c["category_id"] for c in home["categories"]["live"]] == ["1"], home["categories"]["live"])
    s, res = call("GET", f"{D}/device/search?q=eurosport", token=ta)
    check("dold kategori syns inte i sök", res == [], res)
    s, cats = call("GET", f"{A}/api/categories?type=live")
    check("admin ser dolda kategorier", any(c["hidden"] for c in cats), cats)
    call("POST", f"{A}/api/categories/prefs", {"type": "live", "changes": [{"category_id": "2", "hidden": False}]})

    print("Fortsätt titta, sedda och senaste kanaler")
    s, p = call("POST", f"{D}/device/play", {"type": "vod", "id": "502"}, token=ta)
    check("starta film", p["status"] == "ok" and p["resume"] is None, p)
    call("POST", f"{D}/device/heartbeat", {"session_id": p["session_id"], "metrics": {"position_ms": 600000, "duration_ms": 10800000}}, token=ta)
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("filmen syns i Fortsätt titta", [c["id"] for c in home["continue"]] == ["502"], home["continue"])
    s, p2 = call("POST", f"{D}/device/play", {"type": "vod", "id": "502"}, token=ta)
    check("uppspelning vet var du slutade", p2["resume"] and p2["resume"]["position_ms"] == 600000, p2)
    s, det = call("GET", f"{D}/device/vod/502", token=ta)
    check("detaljsidan vet var du slutade", det["resume"]["position_ms"] == 600000, det.get("resume"))
    call("POST", f"{D}/device/stop", {"session_id": p2["session_id"], "metrics": {"position_ms": 10500000, "duration_ms": 10800000}}, token=ta)
    s, home = call("GET", f"{D}/device/home", token=ta)
    s, b = call("GET", f"{D}/device/browse?type=vod&genre=Historia", token=ta)
    check("färdigsedd film försvinner och markeras som sedd", home["continue"] == [] and b["items"][0]["watched"], (home["continue"], b["items"]))
    s, h_adult = call("GET", f"{D}/device/home", token=ta, mode="adult")
    check("fortsätt titta är separat per läge", h_adult["continue"] == [], h_adult["continue"])
    s, p = call("POST", f"{D}/device/play", {"type": "episode", "id": "9001", "series_id": "900", "ext": "mp4",
                                             "name": "Bonusfamiljen S1E1"}, token=ta)
    call("POST", f"{D}/device/heartbeat", {"session_id": p["session_id"], "metrics": {"position_ms": 300000, "duration_ms": 2700000}}, token=ta)
    s, home = call("GET", f"{D}/device/home", token=ta)
    cont = home["continue"]
    check("avsnitt i Fortsätt titta med serienamn", cont and cont[0]["series_id"] == "900" and cont[0]["series_name"] == "Bonusfamiljen", cont)
    s, ser = call("GET", f"{D}/device/series/900", token=ta)
    check("serien visar var du är", ser["progress"].get("9001", {}).get("position_ms") == 300000 and ser.get("plot"), ser.get("progress"))
    s, p = call("POST", f"{D}/device/play", {"type": "live", "id": "103"}, token=ta)
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("senaste kanaler", home["recent_live"] and home["recent_live"][0]["id"] == "103", home["recent_live"])
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"]}, token=ta)

    print("Programguide och catch-up")
    s, nn = call("GET", f"{D}/device/epg-now?ids=101,102", token=ta)
    check("nu-program för flera kanaler", nn["101"]["now"]["title"] == "Rapport" and nn["102"]["now"], nn)
    s, guide = call("GET", f"{D}/device/guide/101", token=ta)
    past = [x for x in guide["programs"] if x["playable"]]
    check("guide med tidigare program", guide["archive_days"] == 3 and len(past) >= 3 and any(x["now"] for x in guide["programs"]), guide)
    s, guide102 = call("GET", f"{D}/device/guide/102", token=ta)
    check("kanal utan arkiv: inget spelbart bakåt", not any(x["playable"] for x in guide102["programs"]), guide102)
    s, cu = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "start": past[0]["start"], "duration": 60,
                                              "name": "Program -3"}, token=ta)
    check("catch-up ger timeshift-adress", cu["status"] == "ok" and "/timeshift/testuser/testpass/60/" in cu["url"], cu)
    call("POST", f"{D}/device/stop", {"session_id": cu.get("session_id")}, token=ta)
    s, bad = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "start": int(time.time()) + 3600, "duration": 60}, token=ta)
    check("catch-up i framtiden nekas", bad["status"] == "error", bad)

    print("Sparade filter")
    s, f = call("POST", f"{D}/device/filters", {"name": "Korta bra", "type": "vod", "params": {"max_duration": 120, "min_rating": 6, "evil": "x"}}, token=ta)
    s, home = call("GET", f"{D}/device/home", token=ta)
    sf = home["saved_filters"]
    check("sparat filter syns i boxen", sf and sf[0]["name"] == "Korta bra" and "evil" not in sf[0]["params"], sf)
    s, ha = call("GET", f"{D}/device/home", token=ta, mode="adult")
    check("sparade filter är separata per läge", ha["saved_filters"] == [], ha["saved_filters"])
    call("DELETE", f"{D}/device/filters/{f['id']}", token=ta)

    print("Profiler per box")
    call("PUT", f"{A}/api/devices/{pb['device_id']}", {"allow_adult": False})
    s, hb = call("GET", f"{D}/device/home", token=tb, mode="adult")
    check("box utan vuxenläge får vanligt läge", hb["mode"] == "normal" and hb["profile"]["allow_adult"] is False, hb["mode"])
    s, groups = call("GET", f"{A}/api/groups")
    gid = groups[0]["id"]
    call("PUT", f"{A}/api/devices/{pb['device_id']}", {"only_group_id": gid})
    s, hb = call("GET", f"{D}/device/home", token=tb)
    check("barnprofil: bara gruppen", [g["name"] for g in hb["groups"]] == ["Barnen"] and hb["categories"]["live"] == [], hb["groups"])
    s, res = call("GET", f"{D}/device/search?q=svt", token=tb)
    check("barnprofil: sök bara i gruppen", res == [], res)
    s, res = call("GET", f"{D}/device/search?q=bamse", token=tb)
    check("barnprofil: hittar innehåll i gruppen", [r["id"] for r in res] == ["505"], res)
    s, bad = call("POST", f"{D}/device/play", {"type": "live", "id": "101"}, token=tb)
    check("barnprofil: kan inte spela annat", bad["status"] == "error", bad)
    s, bad = call("POST", f"{D}/device/play", {"type": "episode", "id": "9001", "series_id": "900"}, token=tb)
    check("barnprofil: serie utanför gruppen nekas", bad["status"] == "error", bad)
    call("POST", f"{A}/api/groups/{gid}/items", {"type": "series", "id": "900"})
    s, ok = call("POST", f"{D}/device/play", {"type": "episode", "id": "9001", "series_id": "900"}, token=tb)
    check("barnprofil: serie i gruppen går att spela", ok["status"] in ("ok", "conflict"), ok)
    if ok.get("session_id"):
        call("POST", f"{D}/device/stop", {"session_id": ok["session_id"]}, token=tb)
    call("PUT", f"{A}/api/devices/{pb['device_id']}", {"only_group_id": None, "allow_adult": True})

    print("Bevakningar")
    s, w = call("POST", f"{A}/api/watches", {"query": "dune", "type": "vod"})
    check("bevakning skapad, befintliga räknas som sedda", s == 201 and w["existing"] == 1, w)
    fake_mode("ok", extra=1)
    call("POST", f"{A}/api/catalog/sync")
    time.sleep(1)
    wait_for(lambda: _sync_done(), timeout=30)
    s, ws = call("GET", f"{A}/api/watches")
    check("ny titel ger träff", ws[0]["hits"] and ws[0]["hits"][0]["name"] == "Dune: Part Three (2026)", ws)
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("träffen syns i boxen", [i["id"] for i in home["watch_hits"]] == ["520"], home["watch_hits"])
    fake_mode("ok", extra=0)

    print("Appuppdatering")
    s, up = call("GET", f"{D}/device/app-update?code=5", token=ta)
    check("ny appversion hittas", up["available"] and up["latest"]["version_code"] == 42, up)
    s, up = call("GET", f"{D}/device/app-update?code=42", token=ta)
    check("ingen uppdatering när versionen är aktuell", up["available"] is False, up)
    s, apk = call("GET", f"{D}/device/app.apk", token=ta, raw=True)
    check("APK levereras via Home Assistant", s == 200 and len(apk) > 100000, len(apk))


def version_13(ta, tb, pa, pb):
    print("Version 1.3: inspelning och timeshift")
    now = int(time.time())
    s, w = call("POST", f"{D}/device/play", {"type": "live", "id": "102"}, token=ta)
    check("box A tittar på kanal 102", w["status"] == "ok", w)
    s, live101 = call("POST", f"{D}/device/play", {"type": "live", "id": "101"}, token=ta)
    check("liveström talar om hur långt arkivet räcker (timeshift)", live101.get("archive_days") == 3, live101)
    s, w = call("POST", f"{D}/device/play", {"type": "live", "id": "102"}, token=ta)
    s, r1 = call("POST", f"{D}/device/recordings", {"stream_id": "101", "title": "Rapport",
                                                     "start": now + 3600, "end": now + 5400}, token=ta)
    check("planera inspelning framåt", s == 201 and r1["source"] == "live" and r1["status"] == "scheduled", r1)
    s, again = call("POST", f"{D}/device/recordings", {"stream_id": "101", "title": "Rapport",
                                                        "start": now + 3600, "end": now + 5400}, token=ta)
    check("samma program två gånger blir en inspelning", s == 200 and again["id"] == r1["id"], again)
    s, bad = call("POST", f"{D}/device/recordings", {"stream_id": "102", "start": now - 7200, "end": now - 3600}, token=ta)
    check("sänt program utan arkiv nekas", s == 400 and "arkiv" in bad["error"], bad)
    s, r2 = call("POST", f"{D}/device/recordings", {"stream_id": "101", "title": "Förr", "start": now - 7200,
                                                     "end": now - 3600}, token=ta)
    check("sänt program spelas in från arkivet", s == 201 and r2["source"] == "archive", r2)
    s, bad = call("POST", f"{D}/device/recordings", {"stream_id": "101", "start": now, "end": now + 8 * 3600}, token=ta)
    check("längre än 6 timmar nekas", s == 400, bad)
    s, bad = call("POST", f"{D}/device/recordings", {"stream_id": "190", "start": now + 60, "end": now + 600}, token=ta)
    check("vuxenkanal kan inte spelas in i vanligt läge", s == 400, bad)
    s, r3 = call("POST", f"{D}/device/recordings", {"stream_id": "190", "title": "X", "start": now + 60,
                                                     "end": now + 600}, token=ta, mode="adult")
    check("vuxenkanal i vuxenläge", s == 201 and r3["adult"] is True, r3)
    s, normal = call("GET", f"{D}/device/recordings", token=ta)
    s, adult = call("GET", f"{D}/device/recordings", token=ta, mode="adult")
    s, everything = call("GET", f"{D}/device/recordings?all=1", token=ta)
    check("inspelningar separeras per läge", r3["id"] not in [r["id"] for r in normal]
          and [r["id"] for r in adult] == [r3["id"]], (normal, adult))
    check("schemaläggaren ser allt", {r1["id"], r2["id"], r3["id"]} <= {r["id"] for r in everything}, everything)
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("antal inspelningar i hemdata", home["recordings"] == 2, home.get("recordings"))
    s, guide = call("GET", f"{D}/device/guide/101", token=ta)
    check("guiden visar vad som går att spela in", all(p["recordable"] for p in guide["programs"] if p["end"] > now), guide)

    print("Inspelning samtidigt som man tittar")
    s, rec = call("POST", f"{D}/device/play", {"type": "live", "id": "101", "recording_id": r1["id"]}, token=ta)
    check("inspelning startar trots att boxen tittar", rec["status"] == "ok" and "/live/" in rec["url"], rec)
    s, hb = call("POST", f"{D}/device/heartbeat", {"session_id": w["session_id"], "metrics": {}}, token=ta)
    check("tittandet fortsätter under inspelningen", hb["stop"] is False, hb)
    s, mine = call("GET", f"{D}/device/recordings", token=ta)
    check("status: spelar in", next(r for r in mine if r["id"] == r1["id"])["status"] == "recording", mine)
    s, ov = call("GET", f"{A}/api/overview")
    check("översikten visar inspelningen", any(x["type"] == "record" for x in ov["sessions"]), ov["sessions"])
    s, conflict = call("POST", f"{D}/device/play", {"type": "live", "id": "103"}, token=tb)
    check("box B får fråga om box A:s tittande", conflict["status"] == "conflict", conflict)
    call("POST", f"{D}/device/stop", {"session_id": w["session_id"]}, token=ta)
    s, bplay = call("POST", f"{D}/device/play", {"type": "live", "id": "103"}, token=tb)
    check("inspelning blockerar inte box B", bplay["status"] == "ok", bplay)
    s, devs = call("GET", f"{A}/api/devices")
    a_dev = next(d for d in devs if d["id"] == pa["device_id"])
    check("box A visas inte som tittande när den bara spelar in", a_dev["playing"] is None, a_dev)
    call("POST", f"{D}/device/stop", {"session_id": bplay["session_id"]}, token=tb)
    s, bad = call("POST", f"{D}/device/play", {"type": "live", "id": "102", "recording_id": r1["id"]}, token=ta)
    check("fel kanal för inspelningen nekas", bad["status"] == "error", bad)
    s, bad = call("POST", f"{D}/device/play", {"type": "live", "id": "101", "recording_id": r1["id"]}, token=tb)
    check("annan box kan inte använda inspelningen", bad["status"] == "error", bad)
    s, arch = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "recording_id": r2["id"],
                                                "start": r2["start"], "duration": 61}, token=ta)
    check("arkivinspelning ger timeshift-adress", arch["status"] == "ok" and "/timeshift/" in arch["url"], arch)
    call("POST", f"{D}/device/stop", {"session_id": arch["session_id"]}, token=ta)

    print("Stoppa och ta bort")
    call("POST", f"{A}/api/recordings/{r1['id']}/stop")
    s, hb = call("POST", f"{D}/device/heartbeat", {"session_id": rec["session_id"], "metrics": {}}, token=ta)
    check("stopp från webbadminen når boxen", hb["stop"] and hb["reason"] == "record_stopped", hb)
    s, done = call("POST", f"{D}/device/recordings/{r1['id']}/status",
                   {"status": "done", "size_bytes": 123456789, "file": "/storage/USB/Inspelningar/a.ts"}, token=ta)
    check("boxen rapporterar färdig inspelning", done["status"] == "done" and done["size_bytes"] == 123456789, done)
    s, alls = call("GET", f"{A}/api/recordings")
    row = next(r for r in alls if r["id"] == r1["id"])
    check("webbadminen visar box och storlek", row["device_name"] and row["size_bytes"] == 123456789, row)
    s, res = call("DELETE", f"{A}/api/recordings/{r1['id']}")
    check("färdig inspelning markeras för radering", res["result"] == "marked", res)
    s, everything = call("GET", f"{D}/device/recordings?all=1", token=ta)
    check("boxen ser att filen ska raderas", next(r for r in everything if r["id"] == r1["id"])["status"] == "deleted", everything)
    call("DELETE", f"{D}/device/recordings/{r1['id']}", token=ta)
    s, everything = call("GET", f"{D}/device/recordings?all=1", token=ta)
    check("boxen tar bort raden när filen är raderad", r1["id"] not in [r["id"] for r in everything], everything)
    s, res = call("DELETE", f"{A}/api/recordings/{r3['id']}")
    check("planerad inspelning tas bort direkt", res["result"] == "removed", res)

    print("Planera från webbadminen")
    s, g = call("GET", f"{A}/api/guide/101")
    future = [p for p in g["programs"] if p["start"] > now]
    s, ar = call("POST", f"{A}/api/recordings", {"device_id": pb["device_id"], "stream_id": "101",
                                                 "title": future[0]["title"], "start": future[0]["start"],
                                                 "end": future[0]["end"]})
    check("planera inspelning på box B från mobilen", s == 201 and ar["device_id"] == pb["device_id"], ar)
    s, bl = call("GET", f"{D}/device/recordings?all=1", token=tb)
    check("box B får schemat", [r["id"] for r in bl] == [ar["id"]], bl)
    s, bad = call("POST", f"{A}/api/recordings", {"stream_id": "101", "start": now + 60, "end": now + 120})
    check("box måste väljas", s == 400, bad)


def library_14():
    print("Version 1.4: biblioteket i webbadminen")
    s, b = call("GET", f"{A}/api/library/browse?type=vod&sort=duration&max_duration=120")
    check("admin: sortera och filtrera som i boxen", [i["id"] for i in b["items"]] == ["505", "503"] and b["total"] == 2, b)
    s, b = call("GET", f"{A}/api/library/browse?type=vod&sort=rating&min_rating=7&min_year=1980&genre=Drama")
    check("admin: kombinerade filter", b["total"] >= 1 and all(i["rating_num"] >= 7 for i in b["items"]), b)
    s, normal = call("GET", f"{A}/api/library/browse?type=vod")
    s, adult = call("GET", f"{A}/api/library/browse?type=vod&adult=1")
    s, both = call("GET", f"{A}/api/library/browse?type=vod&adult=all")
    check("admin: vanligt läge visar inte 18+", "599" not in [i["id"] for i in normal["items"]], normal["total"])
    check("admin: bara 18+", [i["id"] for i in adult["items"]] == ["599"], adult["items"])
    check("admin: allt", both["total"] == normal["total"] + 1, (both["total"], normal["total"]))
    s, page = call("GET", f"{A}/api/library/browse?type=vod&offset=2&limit=2")
    check("admin: sidor", len(page["items"]) == 2 and page["total"] == normal["total"], page)
    s, b = call("GET", f"{A}/api/library/browse?type=live&q=svt&sort=num")
    check("admin: sök kanaler", [i["id"] for i in b["items"]] == ["101", "102"], b)
    s, cats = call("GET", f"{A}/api/library/categories?type=live&adult=0")
    s, acats = call("GET", f"{A}/api/library/categories?type=live&adult=1")
    s, allcats = call("GET", f"{A}/api/library/categories?type=live")
    check("admin: kategorilistan följer innehållsvalet", "3" not in [c["category_id"] for c in cats]
          and [c["category_id"] for c in acats] == ["3"] and "3" in [c["category_id"] for c in allcats], (cats, acats))
    s, b = call("GET", f"{A}/api/library/browse?type=live&adult=1&category_id=3")
    check("admin: vuxenkanaler visas med Bara 18+", [i["id"] for i in b["items"]] == ["190"], b)
    s, g = call("GET", f"{A}/api/library/genres?type=vod")
    check("admin: genrer", "Drama" in [x["name"] for x in g] and "Vuxen" not in [x["name"] for x in g], g)
    s, d = call("GET", f"{A}/api/library/vod/501")
    check("admin: detaljer med handling", s == 200 and d.get("plot") and d.get("duration_min") == 166, d)
    icon = next((i["icon"] for i in normal["items"] if i["icon"]), "")
    check("affischadresser finns", icon.startswith("http"), normal["items"])
    s, png = call("GET", f"{A}/api/img?u={urllib.parse.quote(icon, safe='')}", raw=True)
    check("affisch via servern (https-säkert)", s == 200 and png[:4] == b"\x89PNG", (s, png[:8]))
    s, png2 = call("GET", f"{A}/api/img?u={urllib.parse.quote(icon, safe='')}", raw=True)
    check("affisch från cachen", s == 200 and png2 == png, s)
    s, bad = call("GET", f"{A}/api/img?u={urllib.parse.quote(f'http://127.0.0.1:{FAKE_PORT}/player_api.php', safe='')}")
    check("bildproxyn vägrar annat än bilder", s == 415, (s, bad))
    s, bad = call("GET", f"{A}/api/img?u=file:///etc/passwd")
    check("bildproxyn vägrar lokala filer", s == 400, bad)
    s, bad = call("GET", f"{D}/api/img?u={urllib.parse.quote(icon, safe='')}")
    check("bildproxyn nås inte via box-porten", s == 404, s)


def version_15(ta, tb, pa, pb):
    print("Version 1.5: vuxenregel, historik, nytt tillagt, manuell inspelning, tydligare varningar")
    sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
    from hub.catalog import is_adult_name
    for name in ("XXX | For Adults", "18+ For Adults", "others For Adults", "AI Porn 18+ For Adults", "For Adults"):
        check(f"vuxenkategori: {name}", is_adult_name(name))
    for name in ("Adult Swim", "xXx: Return of Xander Cage", "Hustlers", "Vuxna människor", "18+ Thriller", "Adults in the Room"):
        check(f"inte vuxen: {name}", not is_adult_name(name))

    # Manuell markering av kategori
    call("POST", f"{A}/api/categories/prefs", {"type": "live", "changes": [{"category_id": "2", "adult": True}]})
    s, hn = call("GET", f"{D}/device/home", token=ta)
    check("manuellt markerad vuxenkategori döljs i vanligt läge", "Sport" not in [c["name"] for c in hn["categories"]["live"]], hn["categories"]["live"])
    s, cats = call("GET", f"{A}/api/categories?type=live")
    check("admin ser manuell markering", next(c for c in cats if c["category_id"] == "2")["adult_override"] == 1, cats)
    call("POST", f"{A}/api/categories/prefs", {"type": "live", "changes": [{"category_id": "3", "adult": False}, {"category_id": "2", "adult": None}]})
    s, hn = call("GET", f"{D}/device/home", token=ta)
    names = [c["name"] for c in hn["categories"]["live"]]
    check("avmarkerad kategori syns i vanligt läge, automatisk återställd", "XXX | For Adults" in names and "Sport" in names, names)
    call("POST", f"{A}/api/categories/prefs", {"type": "live", "changes": [{"category_id": "3", "adult": None}]})
    call("POST", f"{A}/api/catalog/sync"); time.sleep(1); wait_for(lambda: _sync_done(), timeout=30)
    s, hn = call("GET", f"{D}/device/home", token=ta)
    check("automatisk markering gäller igen efter synk", "XXX | For Adults" not in [c["name"] for c in hn["categories"]["live"]], hn["categories"]["live"])

    # Historik i vuxenläget: sparas alltid, visas bara med "show"
    s, p = call("POST", f"{D}/device/play", {"type": "vod", "id": "599"}, token=ta, mode="adult")
    check("vuxenfilm startar", p["status"] == "ok", p)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 900000, "duration_ms": 5400000}}, token=ta)
    s, hidden = call("GET", f"{D}/device/home", token=ta, mode="adult")
    s, shown = call("GET", f"{D}/device/home", token=ta, mode="adult", history="show")
    check("utan historik: Fortsätt titta tom", hidden["continue"] == [], hidden["continue"])
    check("med historik: filmen finns (sparades ändå)", [c["id"] for c in shown["continue"]] == ["599"], shown["continue"])
    s, p2 = call("POST", f"{D}/device/play", {"type": "vod", "id": "599"}, token=ta, mode="adult")
    check("utan historik: startar från början", p2["resume"] is None, p2.get("resume"))
    call("POST", f"{D}/device/stop", {"session_id": p2["session_id"]}, token=ta)
    s, p3 = call("POST", f"{D}/device/play", {"type": "vod", "id": "599"}, token=ta, mode="adult", history="show")
    check("med historik: fortsätter där du slutade", p3["resume"] and p3["resume"]["position_ms"] >= 900000, p3.get("resume"))
    call("POST", f"{D}/device/stop", {"session_id": p3["session_id"]}, token=ta)

    # Nytt tillagt: eget datum för titlar som kommit efter första synken
    fake_mode("ok", extra=1)
    call("POST", f"{A}/api/catalog/sync"); time.sleep(1); wait_for(lambda: _sync_done(), timeout=30)
    s, b = call("GET", f"{D}/device/browse?type=vod&sort=added&limit=3", token=ta)
    first = b["items"][0]
    check("nytt tillagt: nyaste först med dagens datum", first["id"] == "520" and abs(first["added"] - time.time()) < 3600, b["items"][:2])
    s, nb = call("GET", f"{D}/device/browse?type=vod&sort=added&new_only=1", token=ta)
    check("nytt tillagt visar bara titlar med TV Hubs eget datum", [i["id"] for i in nb["items"]] == ["520"]
          and nb["items"][0]["first_seen"], nb)
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("raden Nytt: filmer använder TV Hubs datum", [i["id"] for i in home["recent_vod"]] == ["520"], home["recent_vod"])
    s, ab = call("GET", f"{A}/api/library/browse?type=vod&sort=added&new_only=1")
    check("webbadminen: nytt tillagt med TV Hubs datum", [i["id"] for i in ab["items"]] == ["520"], ab)
    s, b2 = call("GET", f"{D}/device/browse?type=live&sort=added&limit=3", token=ta)
    check("nytt tillagt fungerar för kanaler", s == 200 and b2["items"], b2)
    fake_mode("ok", extra=0)

    # Manuell inspelning utan programguide
    now = int(time.time())
    s, r = call("POST", f"{D}/device/recordings", {"stream_id": "190", "title": "Hot Channel HD 19 okt 22:00",
                                                    "start": now, "end": now + 3600}, token=ta, mode="adult")
    check("manuell inspelning av kanal utan programguide", s == 201 and r["source"] == "live", r)
    s, ar = call("POST", f"{A}/api/recordings", {"device_id": pa["device_id"], "stream_id": "104", "title": "",
                                                 "start": now + 7200, "end": now + 9000})
    check("manuell inspelning från webbadminen, namn från kanalen", s == 201 and ar["title"] == "SE: Kanal 5", ar)
    call("DELETE", f"{A}/api/recordings/{r['id']}"); call("DELETE", f"{A}/api/recordings/{ar['id']}")

    # Tydligare varningar
    s, p = call("POST", f"{D}/device/play", {"type": "live", "id": "101"}, token=tb)
    for _ in range(2):
        call("POST", f"{D}/device/report", {"kind": "error", "session_id": p.get("session_id"),
                                            "detail": {"code": "ERROR_CODE_PARSING_CONTAINER_MALFORMED", "message": "Source error"}}, token=tb)
    call("POST", f"{D}/device/stop", {"session_id": p.get("session_id")}, token=tb)
    s, diag = call("GET", f"{A}/api/diagnostics")
    s, devs = call("GET", f"{A}/api/devices")
    bname = next(d["name"] for d in devs if d["id"] == pb["device_id"])
    errs = [f for f in diag["diagnosis"]["findings"] if f["title"].startswith(f"Uppspelningsfel på {bname}")]
    check("fel anger boxen och slås ihop", len(errs) == 1 and "2 gånger" in errs[0]["title"], [f["title"] for f in diag["diagnosis"]["findings"]])
    check("fel förklaras på svenska", errs and "trasig ström" in errs[0]["detail"], errs)
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("hemskärmens rader: nya serier och topprankat", "recent_series" in home and "top_vod" in home
          and all(i.get("rating_num", 0) >= 7.5 for i in home["top_vod"]), (home.get("recent_series"), home.get("top_vod")))
    check("ordet hackar används inte", not any("hack" in f["title"].lower() for f in diag["diagnosis"]["findings"]), diag["diagnosis"]["findings"])


def _up(url):
    try:
        urllib.request.urlopen(url, timeout=1).read()
        return True
    except OSError:
        return False


def _sync_done():
    s, data = call("GET", f"{A}/api/catalog/syncs")
    if data["status"]["running"] or not data["runs"]:
        return None
    return data["runs"][0] if data["runs"][0]["status"] != "running" else None


if __name__ == "__main__":
    main()
