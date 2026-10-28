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
FAKE_HA = os.path.join(ROOT, "tests", "fake_ha.py")
FAKE_PORT, ADMIN, DEVICE = 18091, 18080, 18099
HA_TLS, HA_WS = 18443, 18444
CERT_DIR = tempfile.mkdtemp(prefix="tvhub-cert-")
A = f"http://127.0.0.1:{ADMIN}"
D = f"http://127.0.0.1:{DEVICE}"
FAILS = []
DATA_DIR = None


def call(method, url, body=None, token=None, raw=False, mode=None, history=None, unlock=None, profile=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if profile:
        req.add_header("X-TVHub-Profile", str(profile))
    if unlock:
        req.add_header("X-TVHub-Unlock", unlock)
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
    global DATA_DIR
    DATA_DIR = data_dir
    env = dict(os.environ, TVHUB_PROBE_MIN_GAP="3", DATA_DIR=data_dir, ADMIN_PORT=str(ADMIN), DEVICE_PORT=str(DEVICE),
               CHECK_INTERVAL="2", NET_CHECK_INTERVAL="2", TIMEOUT="3", SYNC_TIMEOUT="2",
               REFERENCE_HOSTS=f"127.0.0.1:{FAKE_PORT}", OPTIONS_PATH="/nonexistent",
               ADMIN_ALLOWED_IPS="127.0.0.1", NO_PROXY="*", no_proxy="*",
               INFO_START_DELAY="1", INFO_PAUSE="0.05", TASTE_CACHE_MIN_AGE="0", GUIDE_CACHE_S="0", SPORT_SCAN_DELAY="3600", SPORT_SCAN_PAUSE="0", APP_RELEASE_BASE=f"http://127.0.0.1:{FAKE_PORT}/release")
    env.pop("HTTPS_PROXY", None); env.pop("https_proxy", None)
    cert, key = os.path.join(CERT_DIR, "cert.pem"), os.path.join(CERT_DIR, "key.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                    "-days", "2", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                   check=True, capture_output=True)
    env.update(HA_WS_URL=f"ws://127.0.0.1:{HA_WS}/core/websocket", SUPERVISOR_TOKEN="sup-token",
               INGRESS_ENTRY="/api/hassio_ingress/INGRESSTOK", REMOTE_CA_FILE=cert,
               SUPERVISOR_API=f"http://127.0.0.1:{HA_WS}")
    fake_ha = subprocess.Popen([sys.executable, FAKE_HA, str(HA_TLS), str(HA_WS), str(ADMIN), cert, key])
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
        media_recordings(ta, tb, pa, pb)
        episode_favorites(ta)
        film_night(ta, pa)
        categories_and_new(ta)
        remote_control(ta, tb, pa, pb, cert)
        remote_access(ta, pa, cert)
        version_21(ta, tb, pa, pb, cert)
        version_25(ta, tb, pa, pb)
        version_26(ta, tb, pa, pb)
        version_27(ta, tb, pa, pb)
        version_28(ta, tb, pa, pb)
        version_29(ta, tb, pa, pb)
        version_291(ta, tb, pa, pb)
        version_292()
        version_294(ta, tb, pa, pb)

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
        fake_ha.terminate()
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
                                                     "start": now + 120, "end": now + 1800}, token=ta)
    check("planera inspelning framåt", s == 201 and r1["source"] == "live" and r1["status"] == "scheduled", r1)
    s, again = call("POST", f"{D}/device/recordings", {"stream_id": "101", "title": "Rapport",
                                                        "start": now + 120, "end": now + 1800}, token=ta)
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
    s, adult = call("GET", f"{D}/device/recordings", token=ta, mode="adult", history="show")
    s, adult_private = call("GET", f"{D}/device/recordings", token=ta, mode="adult")
    check("2.9: vuxeninspelningar syns inte utan historik", adult_private == [], adult_private)
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


def media_recordings(ta, tb, pa, pb):
    print("Spela in filmer och serier")
    s, w = call("POST", f"{D}/device/play", {"type": "live", "id": "102"}, token=ta)
    s, r = call("POST", f"{D}/device/recordings", {"kind": "vod", "id": "501"}, token=ta)
    check("spela in film", s == 201 and r["source"] == "vod" and r["ext"] == "mkv" and r["title"].startswith("Dune"), r)
    s, again = call("POST", f"{D}/device/recordings", {"kind": "vod", "id": "501"}, token=ta)
    check("samma film två gånger blir en inspelning", s == 200 and again["id"] == r["id"], again)
    s, bad = call("POST", f"{D}/device/recordings", {"kind": "vod", "id": "599"}, token=ta)
    check("vuxenfilm kan inte spelas in i vanligt läge", s == 400, bad)
    s, p = call("POST", f"{D}/device/play", {"type": "vod", "id": "501", "recording_id": r["id"]}, token=ta)
    check("filmen hämtas trots att boxen tittar", p["status"] == "ok" and "/movie/" in p["url"] and p["url"].endswith(".mkv"), p)
    s, hb = call("POST", f"{D}/device/heartbeat", {"session_id": w["session_id"], "metrics": {}}, token=ta)
    check("tittandet fortsätter under nedladdningen", hb["stop"] is False, hb)
    s, bad = call("POST", f"{D}/device/play", {"type": "live", "id": "501", "recording_id": r["id"]}, token=ta)
    check("fel strömtyp för inspelningen nekas", bad["status"] == "error", bad)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"]}, token=ta)
    call("POST", f"{D}/device/stop", {"session_id": w["session_id"]}, token=ta)
    s, sr = call("POST", f"{D}/device/recordings", {"kind": "series", "series_id": "900"}, token=ta)
    check("spela in hela serien", s == 201 and sr["created"] == 2, sr)
    s, sr2 = call("POST", f"{D}/device/recordings", {"kind": "series", "series_id": "900"}, token=ta)
    check("serien igen: inga dubbletter", sr2["created"] == 0 and sr2["existing"] == 2, sr2)
    s, mine = call("GET", f"{D}/device/recordings?all=1", token=ta)
    eps = [x for x in mine if x["source"] == "episode"]
    check("avsnitten har namn, serie och format", len(eps) == 2 and all(x["series_id"] == "900" and x["title"] in ("Bonusfamiljen S1E1", "Bonusfamiljen S2E1") for x in eps), eps)
    s, p = call("POST", f"{D}/device/play", {"type": "episode", "id": eps[0]["stream_id"], "recording_id": eps[0]["id"]}, token=ta)
    check("avsnittet hämtas", p["status"] == "ok" and "/series/" in p["url"], p)
    call("POST", f"{D}/device/stop", {"session_id": p.get("session_id")}, token=ta)
    s, ar = call("POST", f"{A}/api/recordings", {"device_id": pb["device_id"], "kind": "vod", "id": "502"})
    check("spela in film från webbadminen", s == 201 and ar["source"] == "vod" and ar["device_id"] == pb["device_id"], ar)
    s, asr = call("POST", f"{A}/api/recordings", {"device_id": pb["device_id"], "kind": "series", "id": "900", "season": "1"})
    check("spela in en säsong från webbadminen", s == 201 and asr["created"] == 1, asr)
    for x in call("GET", f"{A}/api/recordings")[1]:
        if x["source"] in ("vod", "episode"):
            call("DELETE", f"{A}/api/recordings/{x['id']}")


def episode_favorites(ta):
    print("Favoritavsnitt")
    s, r = call("POST", f"{D}/device/favorites", {"type": "episode", "id": "9002", "series_id": "900",
                                                  "name": "Bonusfamiljen S2E1", "ext": "mp4"}, token=ta)
    check("lägg till avsnitt som favorit", s == 200 and r["favorite"] is True, r)
    s, home = call("GET", f"{D}/device/home", token=ta)
    eps = [f for f in home["favorites"] if f["type"] == "episode"]
    check("avsnittet syns bland favoriterna", len(eps) == 1 and eps[0]["series_id"] == "900"
          and eps[0]["name"] == "Bonusfamiljen S2E1" and eps[0]["category_name"] == "Bonusfamiljen", eps)
    s, ser = call("GET", f"{D}/device/series/900", token=ta)
    check("seriesidan vet vilka avsnitt som är favoriter", ser["favorite_episodes"] == ["9002"], ser.get("favorite_episodes"))
    s, ha = call("GET", f"{D}/device/home", token=ta, mode="adult", history="show")
    check("favoritavsnitt syns inte i vuxenläget", not any(f["type"] == "episode" for f in ha["favorites"]), ha["favorites"])
    s, bad = call("POST", f"{D}/device/favorites", {"type": "episode", "id": "1", "series_id": "999999"}, token=ta)
    check("avsnitt i okänd serie nekas", s == 400, bad)
    call("POST", f"{D}/device/favorites", {"type": "episode", "id": "9002", "on": False}, token=ta)
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("ta bort favoritavsnitt", not any(f["type"] == "episode" for f in home["favorites"]), home["favorites"])


def dev(method, path, body=None, token=None, viewer=None, mode=None):
    """Som call() men med tittarläge (X-TVHub-Viewer)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(D + path, data=data, method=method, headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if viewer:
        req.add_header("X-TVHub-Viewer", str(viewer))
    if mode:
        req.add_header("X-TVHub-Mode", mode)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def film_night(ta, pa):
    """1.9: Filmkväll med tittarlägen, tummar, logg, Vill se, dubbletter, import och AI."""
    print("Filmkväll")
    fake_mode("ok", dupes=1)
    call("POST", f"{A}/api/catalog/sync"); time.sleep(1); wait_for(lambda: _sync_done(), timeout=30)
    ok = wait_for(lambda: all((call("GET", f"{D}/device/vod/{i}", token=ta)[1] or {}).get("genre")
                              for i in ("506", "507", "508", "509")), timeout=30)
    check("nya filmer har fått info", ok, ok)
    # Inga riktiga namn i testerna (förrådet är publikt): två testprofiler läggs till.
    for name in ("Testvuxen", "Testbarn"):
        call("POST", f"{A}/api/profiles", {"name": name})
    s, viewers = call("GET", f"{D}/device/viewers", token=ta)
    names = [v["name"] for v in viewers]
    check("profilerna från start (tittarlägena blev profiler)", names == ["Själv", "Tillsammans", "Gäst", "Testvuxen", "Testbarn"], names)
    byname = {v["name"]: v["id"] for v in viewers}
    self_id, kids_id, adult_id = byname["Själv"], byname["Tillsammans"], byname["Testvuxen"]
    check("Med döttrarna är barnvänligt från start", viewers[1]["rules"]["kids"] is True, viewers[1])

    s, night = call("GET", f"{D}/device/night?viewer={self_id}&limit=20", token=ta)
    ids = [i["id"] for i in night["items"]]
    check("filmkväll ger förslag", s == 200 and len(ids) >= 5, night)
    check("samma film bara en gång", len({"501", "506"} & set(ids)) == 1, ids)
    check("svensk version föredras", "506" in ids, ids)
    dune = next(i for i in night["items"] if i["id"] == "506")
    check("antal versioner visas", dune["versions"] == 2, dune)
    check("vuxeninnehåll föreslås aldrig", "599" not in ids, ids)
    check("motivering och fakta finns", all(i["reason"] and i["facts"] for i in night["items"]), night["items"][:2])
    s, kids = call("GET", f"{D}/device/night?viewer={kids_id}&limit=20", token=ta)
    kid_ids = {i["id"] for i in kids["items"]}
    check("barnvänligt: bara barnfilmer", kid_ids and kid_ids <= {"505", "507", "509"}, kid_ids)
    check("barnvänligt: ingen skräck", "508" not in kid_ids, kid_ids)
    s, kids_off = call("GET", f"{D}/device/night?viewer={kids_id}&kids=0&limit=20", token=ta)
    check("barnvänligt går att slå av", "502" in {i["id"] for i in kids_off["items"]}, kids_off["items"])
    s, noanim = call("GET", f"{D}/device/night?viewer={self_id}&anim=none&limit=20", token=ta)
    check("animerat ut", not ({"505", "507", "509"} & {i["id"] for i in noanim["items"]}), noanim["items"])
    s, onlyanim = call("GET", f"{D}/device/night?viewer={self_id}&anim=only&limit=20", token=ta)
    check("bara animerat", {i["id"] for i in onlyanim["items"]} <= {"505", "507", "509"} and onlyanim["items"], onlyanim["items"])
    s, short = call("GET", f"{D}/device/night?viewer={self_id}&max=120&limit=20", token=ta)
    check("kortare än 2 h", not ({"501", "502", "506"} & {i["id"] for i in short["items"]}), short["items"])
    s, swe = call("GET", f"{D}/device/night?viewer={self_id}&swe=1&limit=20", token=ta)
    check("svenskt", {i["id"] for i in swe["items"]} <= {"503", "504", "505", "506"} and swe["items"], swe["items"])

    # Snabbval på ett förslag
    s, _ = call("POST", f"{D}/device/taste", {"action": "seen", "type": "vod", "id": "502", "viewer": self_id}, token=ta)
    s, _ = call("POST", f"{D}/device/taste", {"action": "tonight", "type": "vod", "id": "503", "viewer": self_id}, token=ta)
    s, _ = call("POST", f"{D}/device/taste", {"action": "never", "type": "vod", "id": "504", "viewer": self_id}, token=ta)
    s, n2 = call("GET", f"{D}/device/night?viewer={self_id}&limit=20", token=ta)
    ids2 = {i["id"] for i in n2["items"]}
    check("redan sett, inte i kväll och aldrig tas bort", not ({"502", "503", "504"} & ids2), ids2)
    s, n3 = call("GET", f"{D}/device/night?viewer={adult_id}&limit=20", token=ta)
    check("gäller bara det tittarläget", {"502", "503", "504"} <= {i["id"] for i in n3["items"]}, n3["items"])
    s, n4 = call("GET", f"{D}/device/night?viewer={self_id}&exclude=505,509&limit=20", token=ta)
    check("exclude (lista som fylls på)", not ({"505", "509"} & {i["id"] for i in n4["items"]}), n4["items"])

    # Tummar och motivering
    s, r = call("POST", f"{D}/device/taste", {"action": "rate", "type": "vod", "id": "509", "value": 2, "seen": True,
                                              "viewer": kids_id}, token=ta)
    check("tumme upp sparas", s == 200 and r["value"] == 2, r)
    s, k2 = call("GET", f"{D}/device/night?viewer={kids_id}&limit=20", token=ta)
    k2_ids = [i["id"] for i in k2["items"]]
    check("sedd och älskad föreslås inte igen direkt", "509" not in k2_ids, k2_ids)
    frost = next((i for i in k2["items"] if i["id"] == "507"), None)
    check("motivering bygger på det ni gillat", frost and "Fantastiska Mr Räv" in frost["reason"], frost)
    s, bad = call("POST", f"{D}/device/taste", {"action": "rate", "type": "vod", "id": "507", "value": 5}, token=ta)
    check("ogiltigt betyg nekas", s == 400, bad)
    s, vd = dev("GET", "/device/vod/501", token=ta, viewer=kids_id)
    check("detaljsidan: versioner", len(vd["versions"]) == 2 and vd["versions"][0]["quality"] == 3, vd.get("versions"))
    s, vd = dev("GET", "/device/vod/509", token=ta, viewer=kids_id)
    check("detaljsidan: tummen för tittarläget", vd["my_rating"] == 2, vd.get("my_rating"))
    s, _ = call("POST", f"{D}/device/taste", {"action": "wish", "type": "vod", "id": "508"}, token=ta)
    s, n5 = call("GET", f"{D}/device/night?viewer={adult_id}&limit=20", token=ta)
    check("Vill se kommer först", n5["items"][0]["id"] == "508" and "Vill se" in n5["items"][0]["reason"], n5["items"][:2])
    s, vd = dev("GET", "/device/vod/508", token=ta)
    check("detaljsidan: Vill se", vd["wish"] is True, vd.get("wish"))
    s, _ = call("POST", f"{D}/device/taste", {"action": "unwish", "type": "vod", "id": "508"}, token=ta)

    # Sett klart loggas automatiskt, med tittarläge
    s, p = dev("POST", "/device/play", {"type": "vod", "id": "507"}, token=ta, viewer=adult_id)
    call("POST", f"{D}/device/heartbeat", {"session_id": p["session_id"],
                                           "metrics": {"position_ms": 6000000, "duration_ms": 6120000}}, token=ta)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 6100000, "duration_ms": 6120000}}, token=ta)
    s, log = call("GET", f"{A}/api/seen")
    auto = [r for r in log if r["item_id"] == "507" and r["source"] == "auto"]
    check("sett klart loggas en gång med tittarläge", len(auto) == 1 and auto[0]["viewer_id"] == adult_id, auto)
    s, p = dev("POST", "/device/play", {"type": "vod", "id": "505"}, token=ta)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 4450000, "duration_ms": 4500000}}, token=ta)
    s, log = call("GET", f"{A}/api/seen")
    unknown = [r for r in log if r["item_id"] == "505"]
    check("utan vald profil loggas på boxens standardprofil", unknown and unknown[0]["viewer_id"] == self_id, unknown)
    call("POST", f"{D}/device/taste", {"action": "rate", "type": "vod", "id": "505", "value": 1, "seen": True,
                                       "viewer": kids_id}, token=ta)
    s, log = call("GET", f"{A}/api/seen")
    claimed = [r for r in log if r["item_id"] == "505"]
    check("tumme från en annan profil loggas på den profilen", any(c["viewer_id"] == kids_id for c in claimed), claimed)
    s, p = call("POST", f"{D}/device/play", {"type": "vod", "id": "599"}, token=ta, mode="adult")
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 4100000, "duration_ms": 4200000}}, token=ta)
    s, log = call("GET", f"{A}/api/seen")
    check("vuxeninnehåll loggas aldrig", not [r for r in log if r["item_id"] == "599"], log[:3])
    s, err = call("GET", f"{D}/device/night", token=ta, mode="adult")
    check("ingen filmkväll i vuxenläget", s == 400, err)
    s, err = call("POST", f"{D}/device/taste", {"action": "rate", "type": "vod", "id": "599", "value": 2}, token=ta)
    check("vuxeninnehåll kan inte betygsättas", s == 400, err)

    # Webbadminen: tittarlägen, inställningar, import
    s, v = call("POST", f"{A}/api/viewers", {"name": "Med vänner", "taste": "Gillar komedier",
                                             "rules": {"max_minutes": 110, "exclude_genres": "skräck, krig", "animated": "none"}})
    check("skapa tittarläge", s == 201 and v["rules"]["exclude_genres"] == ["skräck", "krig"] and v["rules"]["max_minutes"] == 110, v)
    s, nv = call("GET", f"{A}/api/night?viewer={v['id']}&limit=20")
    nv_ids = {i["id"] for i in nv["items"]}
    check("tittarlägets regler används", nv_ids and not ({"501", "502", "506", "508", "505", "507", "509"} & nv_ids), nv_ids)
    s, _ = call("PUT", f"{A}/api/viewers/{v['id']}", {"name": "Vänner", "rules": {}})
    s, vs = call("GET", f"{A}/api/viewers")
    check("byt namn på tittarläge", any(x["name"] == "Vänner" for x in vs), vs)
    s, _ = call("DELETE", f"{A}/api/viewers/{v['id']}")
    s, vs = call("GET", f"{A}/api/viewers")
    check("ta bort tittarläge", len(vs) == 5, vs)
    imdb = ("Const,Your Rating,Date Rated,Title,URL,Title Type,IMDb Rating,Runtime (mins),Year,Genres\n"
            "tt1,9,2024-05-01,Oppenheimer,u,movie,8.3,180,2023,Drama\n"
            "tt2,4,2024-05-02,Skräcknatten,u,movie,6,90,2019,Horror\n"
            "tt3,7,2024-05-03,En film som inte finns,u,movie,6,90,2001,Drama\n")
    s, res = call("POST", f"{A}/api/taste/import", {"viewer": adult_id, "text": imdb})
    check("import från IMDb", s == 200 and res["format"] == "imdb" and res["matched"] == 2 and res["rated"] == 2
          and res["unmatched"] == ["En film som inte finns (2001)"], res)
    s, rl = call("GET", f"{A}/api/ratings?viewer={adult_id}")
    vals = {r["item_id"]: r["value"] for r in rl}
    check("IMDb-betyg blir tummar", vals.get("502") == 2 and vals.get("508") == -1, vals)
    lb = "Date,Name,Year,Letterboxd URI,Rating\n2024-06-01,Frost,2013,https://boxd.it/x,4.5\n"
    s, res = call("POST", f"{A}/api/taste/import", {"viewer": kids_id, "text": lb})
    check("import från Letterboxd", res.get("format") == "letterboxd" and res["matched"] == 1, res)
    s, res = call("POST", f"{A}/api/taste/import", {"viewer": kids_id, "text": "a;b\n1;2"})
    check("okänt format förklaras", s == 400, res)
    s, ti = call("GET", f"{A}/api/taste/item?type=vod&id=507")
    check("biblioteket: tummar och sett per tittarläge", ti["available"] and len(ti["viewers"]) == 5
          and any(x["seen"] for x in ti["viewers"]), ti)
    s, res = call("POST", f"{A}/api/rate", {"viewer": self_id, "type": "vod", "id": "501", "action": "seen"})
    s, n6 = call("GET", f"{D}/device/night?viewer={self_id}&limit=20", token=ta)
    check("markera sedd i webbadminen gäller alla versioner", not ({"501", "506"} & {i["id"] for i in n6["items"]}), n6["items"])
    s, res = call("PUT", f"{A}/api/night/settings", {"version_pref": "quality"})
    check("versionsval sparas", res.get("version_pref") == "quality", res)
    call("PUT", f"{A}/api/night/settings", {"version_pref": "swedish"})

    # AI: valfri tjänst
    s, ai = call("GET", f"{A}/api/ai")
    check("AI är av från start", ai["provider"] == "off" and not ai["enabled"], ai)
    s, res = call("POST", f"{A}/api/ai/run")
    check("AI utan tjänst nekas", s == 400, res)
    call("PUT", f"{A}/api/ai", {"provider": "custom", "url": f"http://127.0.0.1:{FAKE_PORT}/ai/v1", "model": "testmodell"})
    s, t = call("POST", f"{A}/api/ai/test")
    check("egen server (OpenAI-standard) utan nyckel", t["ok"] and t["reply"] == "OK", t)
    call("PUT", f"{A}/api/ai", {"key": "fel-nyckel"})
    s, t = call("POST", f"{A}/api/ai/test")
    check("fel nyckel förklaras", not t["ok"] and "401" in t["error"], t)
    call("PUT", f"{A}/api/ai", {"key": "test-key"})
    s, res = call("POST", f"{A}/api/ai/run")
    done = wait_for(lambda: (call("GET", f"{A}/api/ai")[1].get("last_run") or {}).get("results"), timeout=30)
    s, ai = call("GET", f"{A}/api/ai")
    check("AI-förslag räknas för alla tittarlägen", ai["last_run"]["ok"] and len(ai["last_run"]["results"]) == 8
          and "key" not in ai and ai["key_hint"] == "…-key", ai.get("last_run"))
    s, n7 = call("GET", f"{D}/device/night?viewer={adult_id}&limit=10", token=ta)
    check("AI-förslagen kommer först", n7["ai"] and n7["items"][0]["source"] == "ai"
          and n7["items"][0]["reason"].startswith("AI tycker om"), n7["items"][:2])
    call("POST", f"{D}/device/taste", {"action": "never", "type": "vod", "id": n7["items"][0]["id"], "viewer": adult_id}, token=ta)
    s, n8 = call("GET", f"{D}/device/night?viewer={adult_id}&limit=10", token=ta)
    check("AI-förslag följer Aldrig", n7["items"][0]["id"] not in {i["id"] for i in n8["items"]}, n8["items"][:2])
    s, k3 = call("GET", f"{D}/device/night?viewer={kids_id}&limit=20", token=ta)
    check("AI-förslag följer barnvänligt", {i["id"] for i in k3["items"]} <= {"505", "507", "509"}, k3["items"])
    s, ask = call("POST", f"{A}/api/ai/ask", {"viewer": self_id, "type": "vod", "question": "Något spännande?"})
    check("fråga AI:n", s == 200 and ask["answer"].startswith("Här är") and ask["items"] and ask["items"][0]["reason"], ask)
    s, res = call("POST", f"{A}/api/ai/use", {"viewer": self_id, "type": "vod", "picks": ask["picks"][:2]})
    s, n9 = call("GET", f"{D}/device/night?viewer={self_id}&limit=10", token=ta)
    check("chattens förslag blir kvällens lista", [i["id"] for i in n9["items"][:2]] == [p["id"] for p in ask["picks"][:2]], n9["items"][:2])
    s, ex = call("GET", f"{A}/api/ai/export?viewer={kids_id}&type=vod")
    check("kopiera till valfri AI", "Tittarläge: Med döttrarna" in ex["text"] and "| Frost" not in ex["text"] or "Kandidater" in ex["text"], ex["text"][:200])
    s, res = call("POST", f"{A}/api/ai/paste", {"viewer": adult_id, "type": "vod", "text": 'Svar:\n```json\n{"picks":[{"id":"503","reason":"Klassiker"}]}\n```'})
    check("klistra in svar från valfri AI", s == 200 and res["count"] == 1, res)
    s, res = call("POST", f"{A}/api/ai/paste", {"viewer": adult_id, "type": "vod", "text": "Jag föreslår Jönssonligan och Oppenheimer."})
    check("svar utan JSON förklaras", s == 400, res)
    call("PUT", f"{A}/api/ai", {"provider": "anthropic", "url": f"http://127.0.0.1:{FAKE_PORT}/ai/anthropic", "key": "ant-key", "model": "claude-test"})
    s, t = call("POST", f"{A}/api/ai/test")
    check("Anthropic fungerar", t["ok"] and t["reply"] == "OK", t)
    call("PUT", f"{A}/api/ai", {"provider": "openai", "url": "http://127.0.0.1:1/v1", "key": "x", "model": "m"})
    s, t = call("POST", f"{A}/api/ai/test")
    check("tjänst som inte svarar förklaras", not t["ok"] and "Når inte" in t["error"], t)
    call("PUT", f"{A}/api/ai", {"provider": "off"})
    s, n10 = call("GET", f"{D}/device/night?viewer={adult_id}&limit=10", token=ta)
    check("utan AI fungerar filmkväll ändå", s == 200 and n10["items"], n10)
    fake_mode("ok", dupes=0)


def categories_and_new(ta):
    """2.0: egna kategorinamn, startkategori, Nytt med datum, direkt uppdateringskoll."""
    print("Kategorinamn, startkategori och Nytt")
    s, res = call("POST", f"{A}/api/categories/prefs", {"type": "vod", "changes": [{"category_id": "10", "display_name": "Premiär"}]})
    s, cats = call("GET", f"{A}/api/categories?type=vod")
    c10 = next(c for c in cats if c["category_id"] == "10")
    check("eget kategorinamn sparas", c10["name"] == "Premiär" and c10["original_name"] == "Filmer 2026", c10)
    s, b = call("GET", f"{D}/device/browse?type=vod&category_id=10", token=ta)
    check("eget namn syns i appen", b["items"] and all(i["category_name"] == "Premiär" for i in b["items"]), b["items"][:1])
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("eget namn i appens kategorilista", any(c["name"] == "Premiär" for c in home["categories"]["vod"]), home["categories"]["vod"])
    s, res = call("PUT", f"{A}/api/categories/start", {"type": "vod", "category_id": "10"})
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("startkategori skickas till appen", home["start_categories"].get("vod") == "10", home.get("start_categories"))
    s, cats = call("GET", f"{A}/api/categories?type=vod")
    check("startkategori markeras i webbadminen", next(c for c in cats if c["category_id"] == "10")["start"], cats[:2])
    s, res = call("PUT", f"{A}/api/categories/start", {"type": "vod", "category_id": "nope"})
    check("okänd startkategori nekas", s == 400, res)
    s, res = call("PUT", f"{A}/api/categories/start", {"type": "vod", "category_id": None})
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("startkategori kan tas bort", "vod" not in home["start_categories"], home.get("start_categories"))
    s, res = call("POST", f"{A}/api/categories/prefs", {"type": "vod", "changes": [{"category_id": "10", "display_name": ""}]})
    s, cats = call("GET", f"{A}/api/categories?type=vod")
    check("tomt namn = Toucans namn igen", next(c for c in cats if c["category_id"] == "10")["name"] == "Filmer 2026", cats[:2])
    s, new = call("GET", f"{D}/device/new?type=all", token=ta)
    seen = [i.get("first_seen") or 0 for i in new["items"]]
    check("Nytt: nyaste först med datum", s == 200 and new["items"] and seen == sorted(seen, reverse=True) and seen[0] > 0, new["items"][:2])
    s, new = call("GET", f"{D}/device/new?type=nope", token=ta)
    check("Nytt: okänd typ nekas", s == 400, new)
    s, up = call("GET", f"{D}/device/app-update?code=1&force=1", token=ta)
    check("sök efter uppdatering direkt", s == 200 and up["available"], up)
    # Fjärrkontrollens knappar
    s, ch = call("GET", f"{D}/device/channel-by-num?num=3", token=ta)
    check("sifferknappar: kanal med nummer", s == 200 and ch["id"] == "103" and ch["num"] == 3, ch)
    s, ch = call("GET", f"{D}/device/channel-by-num?num=90", token=ta)
    check("sifferknappar: vuxenkanal syns inte i vanligt läge", s == 404, ch)
    s, ch = call("GET", f"{D}/device/channel-by-num?num=abc", token=ta)
    check("sifferknappar: ogiltigt nummer nekas", s == 400, ch)
    s, f1 = call("POST", f"{D}/device/favorites", {"type": "live", "id": "104", "toggle": True}, token=ta)
    s, f2 = call("POST", f"{D}/device/favorites", {"type": "live", "id": "104", "toggle": True}, token=ta)
    check("★-knappen växlar favorit", f1.get("favorite") is True and f2.get("favorite") is False, (f1, f2))
    s, st = call("GET", f"{D}/device/favorite?type=live&id=104", token=ta)
    s2, f3 = call("POST", f"{D}/device/favorites", {"type": "live", "id": "104", "toggle": True}, token=ta)
    s, st2 = call("GET", f"{D}/device/favorite?type=live&id=104", token=ta)
    call("POST", f"{D}/device/favorites", {"type": "live", "id": "104", "on": False}, token=ta)
    check("spelarmenyn ser om kanalen är favorit", st == {"favorite": False} and st2 == {"favorite": True}, (st, st2))


def _poll(token, results, wait=8):
    s, res = call("GET", f"{D}/device/commands?wait={wait}", token=token)
    results.append((s, res))


def remote_control(ta, tb, pa, pb, cert):
    """2.0: fjärrstart, röstkommandon och skript i Home Assistant."""
    import ssl
    import threading
    print("Fjärrstart och röstkommandon")
    ctx = ssl.create_default_context(cafile=cert)
    ok = wait_for(lambda: call("GET", f"{A}/api/voice")[1]["listener"]["connected"], timeout=20)
    check("lyssnar på Home Assistant", ok, call("GET", f"{A}/api/voice")[1]["listener"])
    s, boxes = call("GET", f"{D}/device/boxes", token=ta)
    check("telefonen ser de andra boxarna", [b["id"] for b in boxes] == [pb["device_id"]], boxes)

    results = []
    t = threading.Thread(target=_poll, args=(tb, results)); t.start(); time.sleep(0.5)
    s, res = call("POST", f"{A}/api/play-on", {"device_id": pb["device_id"], "type": "vod", "id": "501"})
    t.join()
    cmds = results[0][1]["commands"]
    check("spela på box från webbadminen når boxen direkt", s == 200 and cmds and cmds[0]["action"] == "play"
          and cmds[0]["id"] == "501" and cmds[0]["auto_resume"], (res, results))
    s, boxes = call("GET", f"{D}/device/boxes", token=ta)
    check("boxen syns som påslagen", boxes[0]["online"], boxes)
    results = []
    t = threading.Thread(target=_poll, args=(tb, results, 1)); t.start(); t.join()
    check("utan kommando: tom lista efter väntan", results[0][1]["commands"] == [], results)

    s, mp = call("GET", f"{A}/api/voice/media-players")
    check("TV-apparater från Home Assistant", mp["available"] and mp["entities"][0]["entity_id"] == "media_player.vardagsrum_tv"
          and "HDMI 1" in mp["entities"][0]["sources"] and len(mp["entities"]) == 1, mp)
    # Något att fortsätta titta på
    s, p = call("POST", f"{D}/device/play", {"type": "vod", "id": "502"}, token=ta)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 600000, "duration_ms": 10800000}}, token=ta)
    s, cmd = call("POST", f"{A}/api/voice", {"name": "Fortsätt titta i sovrummet", "action": "continue", "device_id": pb["device_id"],
                                             "params": {"tv_entity": "media_player.vardagsrum_tv", "tv_source": "HDMI 1", "tv_delay": 8}})
    check("röstkommando sparas och skript skapas", s == 201 and cmd["script_status"] == "ok" and cmd["entity_id"].startswith("script.tv_hub_"), cmd)
    scripts = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://localhost:{HA_TLS}/__scripts"), context=ctx).read())
    sc = scripts.get(cmd["script_id"]) or {}
    seq = sc.get("sequence") or []
    check("skriptet slår på TV:n, byter källa och skickar händelsen",
          sc.get("alias") == "Fortsätt titta i sovrummet" and seq[0]["then"][0]["action"] == "media_player.turn_on"
          and seq[0]["then"][1]["delay"]["seconds"] == 8 and seq[1]["data"]["source"] == "HDMI 1"
          and seq[2] == {"event": "tv_hub_command", "event_data": {"command_id": cmd["id"]}}, sc)
    results = []
    t = threading.Thread(target=_poll, args=(tb, results)); t.start(); time.sleep(0.5)
    fired = json.loads(urllib.request.urlopen(f"https://localhost:{HA_TLS}/__fire?command_id={cmd['id']}", context=ctx).read())
    t.join()
    cmds = results[0][1]["commands"]
    check("Google → skript → händelse → boxen spelar", fired["sent"] >= 1 and cmds and cmds[0]["id"] == "502"
          and cmds[0]["type"] == "vod", (fired, results))
    s, res = call("POST", f"{A}/api/voice/{cmd['id']}/run")
    check("testa kommandot från webbadminen", s == 200 and res["description"].startswith("Film:"), res)
    call("GET", f"{D}/device/commands?wait=0", token=tb)

    s, ser = call("POST", f"{A}/api/voice", {"name": "Bonusfamiljen i sovrummet", "action": "series", "device_id": pb["device_id"],
                                             "params": {"query": "bonusfam"}})
    s, res = call("POST", f"{A}/api/voice/{ser['id']}/run")
    c = res.get("command") or {}
    check("serie: avsnitt med kö, fortsätter där ni var", s == 200 and c.get("type") == "episode" and c.get("series_id") == "900"
          and c.get("queue_ids") and c["queue_ids"][c["queue_index"]] == c["id"] and c.get("auto_resume"), res)
    s, prog = call("POST", f"{A}/api/voice", {"name": "Senaste programmet", "action": "program", "device_id": pb["device_id"],
                                              "params": {"query": "Program", "channel_id": "101"}})
    s, res = call("POST", f"{A}/api/voice/{prog['id']}/run")
    c = res.get("command") or {}
    check("senaste programmet ur arkivet", s == 200 and c.get("type") == "catchup" and "Program -1" in c.get("name", "")
          and c.get("catchup_minutes") == 60, res)
    s, bad = call("POST", f"{A}/api/voice", {"name": "Fel", "action": "program", "device_id": pb["device_id"], "params": {"query": "Rapport"}})
    check("program utan kanal nekas", s == 400, bad)
    s, nope = call("POST", f"{A}/api/voice", {"name": "Finns inte", "action": "series", "device_id": pb["device_id"], "params": {"query": "xyzzy"}})
    s, res = call("POST", f"{A}/api/voice/{nope['id']}/run")
    check("okänd titel förklaras", s == 400 and "Hittade inget" in res.get("error", ""), res)
    s, ch = call("POST", f"{A}/api/voice", {"name": "SVT1", "action": "channel", "device_id": pb["device_id"], "params": {"query": "svt1"}})
    s, res = call("POST", f"{A}/api/voice/{ch['id']}/run")
    check("kanal via namn", res.get("command", {}).get("id") == "101", res)
    s, pa_cmd = call("POST", f"{A}/api/voice", {"name": "Pausa sovrummet", "action": "pause", "device_id": pb["device_id"],
                                                "params": {"tv_entity": "media_player.vardagsrum_tv"}})
    scripts = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://localhost:{HA_TLS}/__scripts"), context=ctx).read())
    check("paus slår inte på TV:n", len(scripts[pa_cmd["script_id"]]["sequence"]) == 1, scripts.get(pa_cmd["script_id"]))
    s, res = call("POST", f"{A}/api/voice/defaults", {"device_id": pa["device_id"]})
    names = [c["name"] for c in res["created"]]
    check("standardkommandon för en box", len(names) == 4 and names[0].startswith("Fortsätt titta i "), names)
    s, res = call("POST", f"{A}/api/voice/defaults", {"device_id": pa["device_id"]})
    check("standardkommandon skapas inte två gånger", res["created"] == [], res)
    s, res = call("POST", f"{A}/api/voice/{cmd['id']}/script")
    check("skriptet kan skapas om och visas som text", res["status"] == "ok" and "tv_hub_command" in res["yaml"], res)
    s, _ = call("DELETE", f"{A}/api/voice/{cmd['id']}")
    scripts = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://localhost:{HA_TLS}/__scripts"), context=ctx).read())
    check("ta bort kommandot tar bort skriptet", cmd["script_id"] not in scripts, list(scripts))
    call("GET", f"{D}/device/commands?wait=0", token=tb)

    # Telefonen: spela på TV:n
    s, res = call("POST", f"{D}/device/send", {"device_id": pb["device_id"], "type": "episode", "id": "9001", "series_id": "900"}, token=ta)
    results = []
    _poll(tb, results, 1)
    c = (results[0][1]["commands"] or [{}])[0]
    check("spela på TV:n från telefonen", s == 200 and c.get("type") == "episode" and c.get("series_id") == "900", (res, results))
    s, res = call("POST", f"{D}/device/send", {"device_id": pb["device_id"], "type": "vod", "id": "599"}, token=ta)
    check("vuxeninnehåll skickas inte till en box", s == 400, res)
    s, res = call("POST", f"{D}/device/send", {"device_id": 999, "type": "vod", "id": "501"}, token=ta)
    check("okänd box nekas", s == 400, res)
    call("GET", f"{D}/device/commands?wait=0", token=tb)


def remote_access(ta, pa, cert):
    """1.8: telefonen utanför hemmet via Nabu Casa (Ingress)."""
    import ssl
    sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
    from hub import remote as rm
    print("Utanför hemmet (Nabu Casa)")
    ctx = ssl.create_default_context(cafile=cert)
    nabu = f"https://localhost:{HA_TLS}"
    ingress = f"{nabu}/api/hassio_ingress/INGRESSTOK"

    def as_user(method, path, uid, name, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(A + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "X-Remote-User-Id": uid,
                                              "X-Remote-User-Name": name})
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    s, _ = as_user("GET", "/api/overview", "u-admin", "admin")
    check("admin i Home Assistant når webbadminen", s == 200, s)
    s, body = as_user("GET", "/api/overview", "u-guest", "lisa")
    check("vanlig användare nekas webbadminen", s == 403, s)
    s, body = as_user("GET", "/", "u-guest", "lisa")
    check("vanlig användare får en sida som förklarar", s == 403 and b"administrat" in body, s)
    s, _ = as_user("GET", "/api/portals", "u-tvhub", "tvhub")
    check("appens användare nekas webbadminen", s == 403, s)
    s, _ = as_user("GET", "/api/overview", "u-okand", "okand")
    check("okänd användare nekas", s == 403, s)

    s, res = call("GET", f"{D}/device/remote", token=ta)
    check("utan inställning: inte tillgängligt", s == 200 and res["available"] is False, res)
    s, res = call("PUT", f"{A}/api/remote", {"url": "http://abc.ui.nabu.casa"})
    check("http-adress nekas", s == 400, res)
    s, res = call("PUT", f"{A}/api/remote", {"token": "kort"})
    check("felaktig nyckel nekas", s == 400, res)
    s, res = call("PUT", f"{A}/api/remote", {"url": f"localhost:{HA_TLS}/lovelace/0",
                                            "token": "app-token-0123456789abcdef", "app_user": "tvhub"})
    check("spara Nabu Casa-uppgifter", s == 200 and res["ready"] and res["url"] == nabu
          and res["token_hint"] == "…cdef" and "token" not in res, res)
    # 2.7 (F02): bara enheter som admin har slagit på det för får Home Assistant-nyckeln
    s, res = call("GET", f"{D}/device/remote", token=ta)
    check("enhet utan tillstånd får ingen nyckel", s == 200 and res["available"] is False and "ha_token" not in res
          and "app-token" not in json.dumps(res), res)
    s, devs = call("GET", f"{A}/api/devices")
    check("fjärråtkomst är avstängd som standard", all(d["remote_allowed"] is False for d in devs), devs)
    s, _ = call("PUT", f"{A}/api/devices/{pa['device_id']}", {"remote_allowed": True})
    s, res = call("GET", f"{D}/device/remote", token=ta)
    check("boxen får uppgifterna hemma", s == 200 and res["available"] and res["url"] == nabu
          and res["ingress"] == "/api/hassio_ingress/INGRESSTOK" and res["ha_token"], res)
    s, res = call("GET", f"{D}/device/remote")
    check("uppgifterna kräver parad box", s == 401, s)

    s, res = call("POST", f"{A}/api/remote/test")
    check("testa vägen: fungerar", s == 200 and res["ok"], res)

    # Som telefonen: logga in, ingress-session, anrop med boxens nyckel
    me, session = rm.ha_session(f"wss://localhost:{HA_TLS}/api/websocket", "app-token-0123456789abcdef", [
        {"type": "auth/current_user"}, {"type": "supervisor/api", "endpoint": "/ingress/session", "method": "post"}],
        context=ctx)
    sid = session["session"]

    def phone(method, path, token=ta, body=None, sess=None):
        headers = {"Cookie": f"ingress_session={sess or sid}", "Content-Type": "application/json"}
        if token:
            headers["X-TVHub-Token"] = token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(ingress + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20, context=ctx) as res:
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw or b"null")
            except ValueError:
                return exc.code, raw

    s, home = phone("GET", "/device/home")
    check("telefonen når TV Hub via Nabu Casa", s == 200 and "favorites" in home, (s, str(home)[:200]))
    s, res = phone("GET", "/device/vod/501")
    check("film via Nabu Casa", s == 200 and res.get("name"), (s, res))
    s, res = phone("POST", "/device/play", body={"type": "vod", "id": "501"})
    check("spela via Nabu Casa ger Toucans adress direkt", s == 200 and str(FAKE_PORT) in res.get("url", ""), (s, res))
    if s == 200 and res.get("session_id"):
        phone("POST", "/device/stop", body={"session_id": res["session_id"], "reason": "test"})
    s, res = phone("GET", "/device/home", token=None)
    check("utan boxens nyckel nekas", s == 401, s)
    s, res = phone("GET", "/device/home", token="fel")
    check("fel boxnyckel nekas", s == 401, s)
    s, res = phone("POST", "/device/pair", token=None, body={"code": "123456"})
    check("parning går inte via Nabu Casa", s == 404, s)
    s, res = phone("GET", "/device/remote")
    check("nyckeln lämnas inte ut via Nabu Casa", s == 404, s)
    s, res = phone("GET", "/api/portals", token=None)
    check("webbadminen nås inte med appens användare", s == 403, s)
    s, res = phone("GET", "/device/home", sess="ogiltig")
    check("ogiltig ingress-session nekas", s == 401, s)
    s, devs = call("GET", f"{A}/api/devices")
    z10 = next(d for d in devs if d["id"] == pa["device_id"])
    check("boxlistan visar Nabu Casa", (z10["last_ip"] or "").startswith("Nabu Casa"), z10)
    s, res = call("GET", f"{A}/api/remote")
    check("senaste användning visas", res["last"] and res["last"]["user"] == "tvhub", res.get("last"))

    s, res = call("PUT", f"{A}/api/remote", {"token": "admin-token-should-not-be-used-xx"})
    s, res = call("PUT", f"{A}/api/remote", {"token": "guest-token-0123456789abc"})
    s, res = call("POST", f"{A}/api/remote/test")
    check("test med annan vanlig användare fungerar", res["ok"], res)
    s, res = call("PUT", f"{A}/api/remote", {"token": "inte-en-riktig-nyckel-123"})
    s, res = call("POST", f"{A}/api/remote/test")
    check("test med ogiltig nyckel förklarar", not res["ok"] and "logga in" in res["summary"], res)
    s, res = call("PUT", f"{A}/api/remote", {"token": "app-token-0123456789abcdef"})


def version_21(ta, tb, pa, pb, cert):
    """2.1: Nytt per dag, PIN för vuxenläget, ta bort från Fortsätt titta, påminnelser."""
    import ssl
    import threading
    print("Version 2.1")
    ctx = ssl.create_default_context(cafile=cert)
    # Nytt per dag
    s, summ = call("GET", f"{D}/device/new-summary", token=ta)
    days = summ.get("days") or []
    check("nytt per dag: dagar med kategorier och antal", s == 200 and days and days[0]["categories"]
          and days[0]["total"] == sum(c["count"] for c in days[0]["categories"]), days[:1])
    names = [c["name"] for d in days for c in d["categories"]]
    check("nytt per dag: inga vuxenkategorier i vanligt läge", not any("Adult" in n or "XXX" in n for n in names), names)
    cat = days[0]["categories"][0]
    s, items = call("GET", f"{D}/device/new?type={cat['type']}&category_id={cat['category_id']}&day={days[0]['start']}", token=ta)
    check("nytt per dag: titlarna för en dag och kategori", s == 200 and len(items["items"]) == cat["count"]
          and all(i["category_id"] == cat["category_id"] for i in items["items"]), (cat, len(items.get("items", []))))
    s, summ_a = call("GET", f"{D}/device/new-summary", token=ta, mode="adult")
    adult_names = [c["name"] for d in summ_a["days"] for c in d["categories"]]
    check("nytt per dag: vuxenläget blandar inte in vanliga kategorier", all(n not in names for n in adult_names), adult_names)

    # PIN-kod för vuxenläget
    s, res = call("PUT", f"{A}/api/adult-pin", {"pin": "12"})
    check("PIN: för kort kod nekas", s == 400, res)
    s, res = call("PUT", f"{A}/api/adult-pin", {"pin": "4321"})
    s, home = call("GET", f"{D}/device/home", token=ta, mode="adult")
    check("PIN: vuxenläge utan upplåsning ger vanligt läge", home["mode"] == "normal" and home["adult_pin"], home["mode"])
    s, res = call("POST", f"{D}/device/adult-unlock", {}, token=ta)
    check("PIN: appen får veta att kod krävs", s == 200 and res["pin_required"] and not res["token"], res)
    s, res = call("POST", f"{D}/device/adult-unlock", {"pin": "0000"}, token=ta)
    check("PIN: fel kod nekas", s == 403, res)
    s, res = call("POST", f"{D}/device/adult-unlock", {"pin": "4321"}, token=ta)
    unlock = res.get("token")
    s, home = call("GET", f"{D}/device/home", token=ta, mode="adult", unlock=unlock)
    check("PIN: rätt kod ger vuxenläget", home["mode"] == "adult", home["mode"])
    s, home = call("GET", f"{D}/device/home", token=tb, mode="adult", unlock=unlock)
    check("PIN: nyckeln gäller bara boxen som låste upp", home["mode"] == "normal", home["mode"])
    call("POST", f"{D}/device/adult-lock", {}, token=ta)
    s, home = call("GET", f"{D}/device/home", token=ta, mode="adult", unlock=unlock)
    check("PIN: låses när vuxenläget stängs", home["mode"] == "normal", home["mode"])
    s, res = call("PUT", f"{A}/api/adult-pin", {"pin": ""})
    s, home = call("GET", f"{D}/device/home", token=ta, mode="adult")
    check("PIN: utan kod som förut", home["mode"] == "adult" and not home["adult_pin"], home["mode"])

    # Ta bort från Fortsätt titta
    s, p = call("POST", f"{D}/device/play", {"type": "vod", "id": "503"}, token=ta)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 900000, "duration_ms": 7200000}}, token=ta)
    s, home = call("GET", f"{D}/device/home", token=ta)
    had = any(c["id"] == "503" for c in home["continue"])
    s, res = call("POST", f"{D}/device/continue/remove", {"type": "vod", "id": "503"}, token=ta)
    s, home = call("GET", f"{D}/device/home", token=ta)
    check("ta bort från Fortsätt titta", had and res["removed"] == 1 and not any(c["id"] == "503" for c in home["continue"]), res)

    # Påminnelser
    s, sv = call("GET", f"{A}/api/reminders/notify-services")
    check("påminnelser: notify-tjänster från Home Assistant", sv["services"] == ["mobile_app_telefon"], sv)
    s, res = call("PUT", f"{A}/api/reminders/settings", {"notify": "notify.mobile_app_telefon"})
    check("påminnelser: tjänsten sparas", res == {"notify": "mobile_app_telefon"}, res)
    s, res = call("POST", f"{A}/api/reminders/test")
    check("påminnelser: testnotis", s == 200, res)
    start = int(time.time()) + 20
    s, rem = call("POST", f"{D}/device/reminders", {"stream_id": "101", "title": "Rapport", "start": start, "end": start + 1800}, token=tb)
    check("påminnelse sparas", s == 201 and rem["channel"] == "SE: SVT1 HD", rem)
    s, again = call("POST", f"{D}/device/reminders", {"stream_id": "101", "title": "Rapport", "start": start, "end": start + 1800}, token=tb)
    check("samma påminnelse två gånger blir en", s == 200 and again["id"] == rem["id"], again)
    s, res = call("POST", f"{D}/device/reminders", {"stream_id": "190", "title": "X", "start": start, "end": start + 600}, token=tb)
    check("påminnelse om vuxenkanal nekas i vanligt läge", s == 400, res)
    s, res = call("POST", f"{D}/device/reminders", {"stream_id": "101", "title": "Gammalt", "start": start - 3600, "end": start}, token=tb)
    check("påminnelse om program som börjat nekas", s == 400, res)
    s, lst = call("GET", f"{D}/device/reminders", token=tb)
    check("boxen ser sina påminnelser", [r["id"] for r in lst] == [rem["id"]], lst)
    got = []

    def poll():
        end = time.time() + 45
        while time.time() < end and not got:
            s, r = call("GET", f"{D}/device/commands?wait=20", token=tb)
            got.extend(c for c in (r or {}).get("commands", []) if c["action"] == "reminder")
    t = threading.Thread(target=poll); t.start(); t.join()
    check("påminnelsen når boxen när programmet börjar", got and got[0]["title"] == "Rapport" and got[0]["channel_id"] == "101", got)
    notes = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://localhost:{HA_TLS}/__notifies"), context=ctx).read())
    check("påminnelsen skickas till telefonen", any(n[0] == "services/notify/mobile_app_telefon" and "Rapport" in n[1]["title"] for n in notes), notes)
    s, lst = call("GET", f"{D}/device/reminders", token=tb)
    check("skickad påminnelse försvinner ur listan", lst == [], lst)
    s, rem2 = call("POST", f"{D}/device/reminders", {"stream_id": "102", "title": "Sport", "start": start + 7200, "end": start + 9000}, token=tb)
    s, res = call("DELETE", f"{D}/device/reminders/{rem2['id']}", token=ta)
    check("annan box kan inte ta bort påminnelsen", s == 404, res)
    s, res = call("DELETE", f"{D}/device/reminders/{rem2['id']}", token=tb)
    check("påminnelse tas bort", s == 200, res)


def version_25(ta, tb, pa, pb):
    """2.5: profiler, PIN per box, Mina program."""
    import time as _t
    print("Version 2.5: profiler och Mina program")
    s, data = call("GET", f"{A}/api/profiles")
    profs = {p["name"]: p for p in data["profiles"]}
    check("profiler: Själv, Tillsammans, Gäst och testprofilerna", list(profs) == ["Själv", "Tillsammans", "Gäst", "Testvuxen", "Testbarn"], list(profs))
    owner, child, guest = profs["Själv"]["id"], profs["Testbarn"]["id"], profs["Gäst"]["id"]
    check("bara ägaren får vuxenläget, Gäst är gäst", profs["Själv"]["allow_adult"] and not profs["Testbarn"]["allow_adult"]
          and profs["Gäst"]["guest"], profs)
    check("PIN-hash lämnas aldrig ut", all("pin_hash" not in p and "pin_salt" not in p for p in data["profiles"]), data["profiles"][0])
    s, mormor = call("POST", f"{A}/api/profiles", {"name": "Mormor", "color": "#8B5CF6"})
    check("lägg till profil", s == 201 and mormor["name"] == "Mormor", mormor)
    s, res = call("POST", f"{A}/api/profiles", {"name": "mormor"})
    check("samma namn två gånger nekas", s == 400, res)
    s, upd = call("PUT", f"{A}/api/profiles/{mormor['id']}", {"name": "Farmor", "pin": "2468"})
    check("ändra profil och sätt PIN", upd["name"] == "Farmor" and upd["has_pin"], upd)
    s, res = call("POST", f"{D}/device/profile-check", {"profile_id": mormor["id"], "pin": "1111"}, token=ta)
    check("fel profil-PIN nekas", s == 403, res)
    s, res = call("POST", f"{D}/device/profile-check", {"profile_id": mormor["id"], "pin": "2468"}, token=ta)
    check("rätt profil-PIN godtas", s == 200 and res["ok"], res)
    s, res = call("DELETE", f"{A}/api/profiles/{mormor['id']}")
    check("ta bort profil", s == 200 and len(call("GET", f"{A}/api/profiles")[1]["profiles"]) == 5, res)
    s, res = call("DELETE", f"{A}/api/profiles/{owner}")
    check("sista profilen med vuxenläge kan inte tas bort", s == 400, res)

    # Egna favoriter och Fortsätt titta per profil
    call("POST", f"{D}/device/favorites", {"type": "live", "id": "102", "on": True}, token=ta, profile=child)
    s, hi = call("GET", f"{D}/device/home", token=ta, profile=child)
    s, hh = call("GET", f"{D}/device/home", token=ta, profile=owner)
    check("favoriter är per profil", any(f["id"] == "102" for f in hi["favorites"]) and not any(f["id"] == "102" for f in hh["favorites"]),
          ([f["id"] for f in hi["favorites"]], [f["id"] for f in hh["favorites"]]))
    check("hemdatan talar om vem som tittar", hi["active_profile"]["id"] == child and len(hi["profiles"]) == 5, hi["active_profile"])
    s, p = call("POST", f"{D}/device/play", {"type": "vod", "id": "508"}, token=ta, profile=child)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 700000, "duration_ms": 6000000}}, token=ta)
    s, hi = call("GET", f"{D}/device/home", token=ta, profile=child)
    s, hh = call("GET", f"{D}/device/home", token=ta, profile=owner)
    check("Fortsätt titta är per profil", any(c["id"] == "508" for c in hi["continue"]) and not any(c["id"] == "508" for c in hh["continue"]),
          ([c["id"] for c in hi["continue"]], [c["id"] for c in hh["continue"]]))
    s, p = call("POST", f"{D}/device/play", {"type": "vod", "id": "509"}, token=ta, profile=guest)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 700000, "duration_ms": 6000000}}, token=ta)
    s, hg = call("GET", f"{D}/device/home", token=ta, profile=guest)
    check("gäst: inget sparas och inget visas", hg["continue"] == [] and hg["favorites"] == [], (hg["continue"], hg["favorites"]))
    s, res = call("POST", f"{D}/device/favorites", {"type": "live", "id": "101", "on": True}, token=ta, profile=guest)
    check("gäst kan inte spara favoriter", s == 400, res)

    # Vuxenläge: profil och PIN per box
    s, h1 = call("GET", f"{D}/device/home", token=ta, mode="adult", profile=child)
    check("barnets profil kan inte slå på vuxenläget", h1["mode"] == "normal", h1["mode"])
    s, res = call("POST", f"{D}/device/adult-unlock", {}, token=ta, profile=child)
    check("barnets profil får ingen upplåsning", s == 403 and "profil" in res["error"], res)
    s, h2 = call("GET", f"{D}/device/home", token=ta, mode="adult", profile=owner)
    check("ägaren kan (ingen kod satt)", h2["mode"] == "adult", h2["mode"])
    call("PUT", f"{A}/api/adult-pin", {"pin": "4321"})
    s, h3 = call("GET", f"{D}/device/home", token=ta, mode="adult", profile=owner)
    check("box som kräver PIN: vanligt läge utan kod", h3["mode"] == "normal" and h3["adult_pin"], h3["mode"])
    call("PUT", f"{A}/api/devices/{pa['device_id']}", {"pin_required": False})
    s, h4 = call("GET", f"{D}/device/home", token=ta, mode="adult", profile=owner)
    s, un = call("POST", f"{D}/device/adult-unlock", {}, token=ta)
    check("box utan PIN-krav: vuxenläge utan kod", h4["mode"] == "adult" and not h4["adult_pin"] and un["pin_required"] is False, (h4["mode"], un))
    s, devs = call("GET", f"{A}/api/devices")
    da = next(d for d in devs if d["id"] == pa["device_id"])
    check("webbadminen visar PIN-kravet per box", da["pin_required"] is False, da)
    call("PUT", f"{A}/api/devices/{pa['device_id']}", {"pin_required": True})
    call("PUT", f"{A}/api/adult-pin", {"pin": ""})

    # Standardprofil per box
    together = profs["Tillsammans"]["id"]
    s, res = call("PUT", f"{A}/api/devices/{pb['device_id']}", {"default_profile_id": together, "revert_hours": 4})
    s, hb = call("GET", f"{D}/device/home", token=tb)
    check("boxens standardprofil och återgång", hb["active_profile"]["id"] == together and hb["box"]["revert_hours"] == 4
          and hb["box"]["default_profile_id"] == together, (hb["active_profile"], hb["box"]))
    s, res = call("PUT", f"{A}/api/devices/{pb['device_id']}", {"revert_hours": 99})
    check("orimlig återgångstid nekas", s == 400, res)

    # Vuxenlägets tid utan knapptryck, per box (standard 15 minuter)
    s, hb = call("GET", f"{D}/device/home", token=tb)
    check("vuxenlägets tid är 15 minuter som standard", hb["box"]["adult_idle_minutes"] == 15, hb["box"])
    s, res = call("PUT", f"{A}/api/devices/{pb['device_id']}", {"adult_idle_minutes": 60})
    s, hb = call("GET", f"{D}/device/home", token=tb)
    check("vuxenlägets tid kan ändras per box", s == 200 and hb["box"]["adult_idle_minutes"] == 60, hb["box"])
    s, hb2 = call("GET", f"{D}/device/home", token=ta)
    check("andra boxen behåller sin tid", hb2["box"]["adult_idle_minutes"] == 15, hb2["box"])
    for bad in (0, 181, -5):
        s, res = call("PUT", f"{A}/api/devices/{pb['device_id']}", {"adult_idle_minutes": bad})
        check(f"orimlig vuxenlägestid {bad} nekas", s == 400, res)
    call("PUT", f"{A}/api/devices/{pb['device_id']}", {"adult_idle_minutes": 15})

    # Mina program
    fake_mode("ok", news=1)
    s, sug = call("POST", f"{A}/api/my-programs/suggest", {"profile_id": owner})
    names = [p["name"] for p in sug["created"]]
    check("förslagen Rapport, TV4 Nyheterna och Robinson", names == ["Rapport", "TV4 Nyheterna", "Robinson"], sug)
    s, again = call("POST", f"{A}/api/my-programs/suggest", {"profile_id": owner})
    check("förslagen läggs inte till två gånger", again["created"] == [], again)
    s, res = call("POST", f"{A}/api/my-programs", {"profile_id": owner, "mode": "main", "channel_id": "101", "query": "Rapport"})
    check("huvudsändning utan tid nekas", s == 400, res)
    now = int(_t.time())
    base = now - now % 3600
    nyh_start = base - 2 * 3600
    clock = _t.strftime("%H:%M", _t.localtime(nyh_start))
    s, mp = call("POST", f"{A}/api/my-programs", {"profile_id": owner, "name": "Nyheterna", "mode": "main", "channel_id": "101",
                                                  "query": "tv4 nyheterna", "times": [clock]})
    check("huvudsändning sparas", s == 201 and mp["times"] == [clock], mp)
    s, progs = call("GET", f"{D}/device/my-programs", token=ta, profile=owner)
    by = {p["title"]: p for p in progs["programs"]}
    rap = by.get("Rapport", {}).get("command") or {}
    check("Rapport: sänds nu -> direkt", rap.get("type") == "live", by.get("Rapport"))
    nyh = by.get("Nyheterna", {}).get("command") or {}
    check("huvudsändningen ur arkivet", nyh.get("type") == "catchup" and nyh.get("catchup_start") == nyh_start, by.get("Nyheterna"))
    s, progs_child = call("GET", f"{D}/device/my-programs", token=ta, profile=child)
    check("snabbvalen är per profil", progs_child["programs"] == [], progs_child)
    # TV4-kanalen i testet saknar arkiv: Robinson på TV4 förklarar varför
    rob = by.get("Robinson", {})
    check("utan arkiv: förklaring i stället för fel", "error" in rob and rob["subtitle"], rob)
    s, rob101 = call("POST", f"{A}/api/my-programs", {"profile_id": owner, "name": "Robinson SVT", "mode": "archive",
                                                      "channel_id": "101", "query": "Robinson"})
    s, progs = call("GET", f"{D}/device/my-programs", token=ta, profile=owner)
    r = next(p for p in progs["programs"] if p["title"] == "Robinson SVT")
    check("arkiv utan sett: senaste avsnittet", r["command"]["catchup_start"] == base - 3600, r)
    older = base - 3 * 3600
    s, p = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "start": older, "duration": 60}, token=ta, profile=owner)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 3590000, "duration_ms": 3600000}}, token=ta)
    s, progs = call("GET", f"{D}/device/my-programs", token=ta, profile=owner)
    r = next(p for p in progs["programs"] if p["title"] == "Robinson SVT")
    check("arkiv: nästa avsnitt efter det senast sedda", r["command"]["catchup_start"] == base - 3600 and r["command"]["start_ms"] == 0, r)
    s, p = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "start": base - 3600, "duration": 60}, token=ta, profile=owner)
    call("POST", f"{D}/device/stop", {"session_id": p["session_id"], "metrics": {"position_ms": 600000, "duration_ms": 3600000}}, token=ta)
    s, progs = call("GET", f"{D}/device/my-programs", token=ta, profile=owner)
    r = next(p for p in progs["programs"] if p["title"] == "Robinson SVT")
    check("arkiv: påbörjat avsnitt fortsätter där du var", r["command"]["start_ms"] == 600000 and "fortsätt" in r["subtitle"], r)
    s, hh = call("GET", f"{D}/device/home", token=ta, profile=owner)
    check("program ur arkivet hamnar inte i Fortsätt titta", all(c["type"] in ("vod", "episode") for c in hh["continue"]), hh["continue"])
    s, res = call("DELETE", f"{A}/api/my-programs/{rob101['id']}")
    check("ta bort snabbval", s == 200, res)
    fake_mode("ok", news=0)


def _scan_and_wait():
    call("POST", f"{A}/api/sports/scan")
    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(0.5)
        st = call("GET", f"{A}/api/sports")[1]["status"]
        if not st["running"] and st["last_scan"] and st["finished"] and st["finished"] >= st["started"]:
            return st
    return st


def version_26(ta, tb, pa, pb):
    """2.6: Mina sporter, När går …?, matchnotis, inspelning av lagets matcher, röststart, sensor, TV-guiden."""
    print("Version 2.6: Mina sporter")
    fake_mode("ok", sport=1, news=0)
    urllib.request.urlopen(f"http://127.0.0.1:{FAKE_PORT}/__epg_calls").read()
    s, sp = call("GET", f"{A}/api/sports")
    names = [i["name"] for i in sp["interests"]]
    check("intressena från början", names == ["Golf", "Ridsport", "Sverige, fotbollslandslagen"], names)
    # Lag läggs till av användaren (påhittade lag i testerna).
    call("POST", f"{A}/api/sports", {"name": "Stadens Delfiner", "kind": "team",
                                     "keywords": "stadens delfiner\ndelfiner + basket", "exclude": "miami\nnfl"})
    call("POST", f"{A}/api/sports", {"name": "Testlaget FC", "kind": "team", "keywords": "testlaget fc\ntestlaget + allsvenskan"})
    ids = {i["name"]: i["id"] for i in call("GET", f"{A}/api/sports")[1]["interests"]}
    check("svenska kategorier och sportkategorier söks automatiskt", {c["name"] for c in sp["categories"] if c["auto"]}
          == {"SE | Sverige", "Sport"}, sp["categories"])
    st = _scan_and_wait()
    calls = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{FAKE_PORT}/__epg_calls").read())["calls"]
    check("programguiden hämtas för kanalerna, aldrig vuxenkanaler", st["channels"] == 7 and "190" not in calls
          and len(calls) == 7, (st, calls))
    s, ov = call("GET", f"{D}/device/sports", token=ta)
    now_t = [e["title"] for e in ov["now"]]
    up_t = [e["title"] for e in ov["upcoming"]]
    past_t = [e["title"] for e in ov["past"]]
    check("just nu: Testlaget FC och golf", "Fotboll: Testlaget FC - AIK" in now_t and "Golf: Scandinavian Mixed" in now_t, now_t)
    check("kommande: lagets match en gång (två kanaler blir en sändning)",
          up_t.count("Basket: Stadens Delfiner - Borås") == 1, up_t)
    dol = next(e for e in ov["upcoming"] if e["title"] == "Basket: Stadens Delfiner - Borås")
    check("… med den andra kanalen som 'även på'", len(dol["also"]) == 1, dol)
    check("laget med samma namn i annan sport och trav räknas inte", "NFL: Miami Delfiner - New York Jets" not in up_t
          and not any("Trav" in t for t in up_t), up_t)
    check("ridsport: hästhoppning", "Hästhoppning: Falsterbo Horse Show" in up_t, up_t)
    sv = next((e for e in ov["past"] if e["title"] == "Fotboll: Sverige - Danmark"), None)
    check("i efterhand: landskampen ur arkivet", sv is not None and sv["command"]["type"] == "catchup"
          and sv["command"]["id"] == "101", ov["past"])
    check("i efterhand bara det som finns i arkivet", "Golf: PGA Tour" not in past_t, past_t)
    ifk = next(e for e in ov["now"] if e["title"].startswith("Fotboll: Testlaget"))
    check("sänds nu: spela kanalen", ifk["command"] == {"action": "play", "type": "live", "id": "105",
          "name": "C More Fotboll", "icon": ifk["icon"]} and ifk["interests"] == ["Testlaget FC"], ifk)
    counts = {i["name"]: i["count"] for i in ov["interests"]}
    check("antal per intresse", counts["Golf"] == 1 and counts["Stadens Delfiner"] == 2, counts)
    s, one = call("GET", f"{D}/device/sports?interest={ids['Testlaget FC']}", token=ta)
    check("filtrera på ett intresse", [e["title"] for e in one["now"]] == ["Fotboll: Testlaget FC - AIK"]
          and one["upcoming"] == [], one)

    s, w = call("GET", f"{D}/device/when?q=sverige%20danmark", token=ta)
    check("När går …?: hittar landskampen i arkivet", [e["title"] for e in w["past"]] == ["Fotboll: Sverige - Danmark"], w)
    s, w = call("GET", f"{D}/device/when?q=dune", token=ta)
    check("När går …?: filmer i biblioteket också", any(i["name"].startswith("Dune") for i in w["library"]), w["library"])
    s, w = call("GET", f"{D}/device/when?q=x", token=ta)
    check("När går …?: för kort sökning", s == 400, w)

    # Egna sökord
    s, bad = call("POST", f"{A}/api/sports", {"name": "Tom", "keywords": ""})
    check("intresse utan sökord nekas", s == 400, bad)
    s, mine = call("POST", f"{A}/api/sports", {"name": "Basket", "keywords": "basket", "exclude": "nfl", "title_only": True})
    s, pre = call("GET", f"{A}/api/sports/preview/{mine['id']}")
    check("nytt intresse matchas direkt", len(pre["upcoming"]) == 2, pre)
    call("DELETE", f"{A}/api/sports/{mine['id']}")

    # Röststart
    s, cmd = call("POST", f"{A}/api/voice", {"name": "Matchen", "device_id": pa["device_id"], "action": "sport",
                                             "params": {"interest_id": ids["Testlaget FC"]}})
    s, run = call("POST", f"{A}/api/voice/{cmd['id']}/run")
    check("röststart: matchen som sänds nu", s == 200 and run["command"]["id"] == "105" and run["command"]["type"] == "live", run)
    call("GET", f"{D}/device/commands?wait=0", token=ta)
    s, cmd2 = call("POST", f"{A}/api/voice", {"name": "Delfinerna", "device_id": pa["device_id"], "action": "sport",
                                              "params": {"interest_id": ids["Stadens Delfiner"]}})
    s, run = call("POST", f"{A}/api/voice/{cmd2['id']}/run")
    check("röststart utan match nu: säger när nästa är", s == 400 and "Nästa:" in run["error"], run)
    call("DELETE", f"{A}/api/voice/{cmd['id']}")
    call("DELETE", f"{A}/api/voice/{cmd2['id']}")

    # Sensor
    s, summ = call("GET", f"{D}/device/summary", token=ta)
    check("sensorn: nästa match", summ["next_match"] and summ["next_match"]["live"], summ.get("next_match"))

    # Notis när matchen börjar och inspelning av lagets matcher
    call("PUT", f"{A}/api/sports/settings", {"notify_devices": [pa["device_id"]], "lead_minutes": 5, "quiet_from": "", "quiet_to": ""})
    call("PUT", f"{A}/api/sports/{ids['Stadens Delfiner']}", {"notify": True, "record_device_id": pb["device_id"]})
    _scan_and_wait()
    time.sleep(1.5)
    s, res = call("GET", f"{D}/device/commands?wait=0", token=ta)
    rem = [c for c in res["commands"] if c["action"] == "reminder"]
    check("matchen börjar strax: boxen får frågan", len(rem) == 1 and rem[0]["title"] == "Basket: Stadens Delfiner - Luleå", res)
    _scan_and_wait()
    time.sleep(1.5)
    s, res = call("GET", f"{D}/device/commands?wait=0", token=ta)
    check("frågan skickas bara en gång", not [c for c in res["commands"] if c["action"] == "reminder"], res)
    s, recs = call("GET", f"{D}/device/recordings", token=tb)
    dol_recs = [r for r in recs if "Delfiner" in r["title"]]
    check("lagets matcher planeras som inspelningar på vald box", len(dol_recs) == 2
          and all(r["status"] == "scheduled" for r in dol_recs), recs)
    borås = next(r for r in dol_recs if "Borås" in r["title"])
    check("inspelningen börjar före och slutar efter sändningen", borås["start"] == dol["start"] - 120
          and borås["end"] == dol["end"] + 900, borås)
    call("DELETE", f"{D}/device/recordings/{borås['id']}", token=tb)
    _scan_and_wait()
    s, recs = call("GET", f"{D}/device/recordings", token=tb)
    check("borttagen inspelning planeras inte igen", not any("Borås" in r["title"] and r["status"] == "scheduled" for r in recs), recs)
    call("PUT", f"{A}/api/sports/{ids['Stadens Delfiner']}", {"notify": False, "record_device_id": None})
    for r in recs:
        if "Delfiner" in r["title"]:
            call("DELETE", f"{D}/device/recordings/{r['id']}", token=tb)

    # TV-guiden
    s, grid = call("GET", f"{D}/device/epg-grid?ids=101,105,190", token=ta)
    chans = {c["id"]: c for c in grid["channels"]}
    check("TV-guiden: program per kanal, aldrig vuxenkanaler i vanligt läge", set(chans) == {"101", "105"}
          and len(chans["101"]["programs"]) >= 4 and chans["101"]["archive_days"] == 3, grid)
    s, h = call("GET", f"{D}/device/sports", token=ta, mode="adult")
    check("vuxenläget: inga sporter", h["now"] == [] and h["upcoming"] == [], h)
    fake_mode("ok", sport=0)


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


def _threads(count, fn):
    """Kör fn(i) i count trådar som startar samtidigt. Returnerar resultaten i ordning."""
    import threading
    barrier = threading.Barrier(count)
    out = [None] * count

    def run(i):
        barrier.wait()
        try:
            out[i] = fn(i)
        except Exception as exc:  # noqa: BLE001
            out[i] = ("fel", repr(exc))

    ts = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


def version_27(ta, tb, pa, pb):
    """2.7: åtgärder efter den oberoende revisionen (F01–F17). Negativa tester och samtidighetstester."""
    import http.server
    import socket
    import sqlite3
    import threading
    sys.path.insert(0, os.path.join(ROOT, "tv_hub", "app"))
    from hub import limits, remote as rm, updates, devices as dv
    from hub.config import CFG
    print("Version 2.7: revisionsåtgärder")
    call("PUT", f"{A}/api/adult-pin", {"pin": ""})
    s, data = call("GET", f"{A}/api/profiles")
    profs = {p["name"]: p for p in data["profiles"]}
    owner, child = profs["Själv"]["id"], profs["Testbarn"]["id"]

    def stop_all():
        s, ov = call("GET", f"{A}/api/overview")
        for x in ov.get("sessions", []):
            tok = ta if x["device_id"] == pa["device_id"] else tb
            call("POST", f"{D}/device/stop", {"session_id": x["id"]}, token=tok)

    stop_all()

    # F01: mätvärden från boxen sanerade
    s, p = call("POST", f"{D}/device/play", {"type": "live", "id": "101"}, token=ta)
    evil = {"stalls": "<img src=x onerror=alert(1)>", "bandwidth_bps": {"a": 1}, "portal": "<b>x</b>", "resolution": "<script>",
            "evil": "x", "position_ms": True, "stall_ms": 10 ** 30}
    call("POST", f"{D}/device/heartbeat", {"session_id": p["session_id"], "metrics": evil}, token=ta)
    s, ov = call("GET", f"{A}/api/overview")
    m = next(x["metrics"] for x in ov["sessions"] if x["id"] == p["session_id"])
    check("F01: osäkra mätvärden kastas", set(m) <= {"portal"} and "<" not in json.dumps(m), m)
    call("POST", f"{D}/device/heartbeat", {"session_id": p["session_id"],
                                           "metrics": {"bandwidth_bps": 8e6, "stalls": 2, "resolution": "1920x1080", "cast": True}}, token=ta)
    s, ov = call("GET", f"{A}/api/overview")
    m = next(x["metrics"] for x in ov["sessions"] if x["id"] == p["session_id"])
    check("F01: normala mätvärden fungerar", m == {"bandwidth_bps": 8000000, "stalls": 2, "resolution": "1920x1080", "cast": True}, m)
    stop_all()

    # F03: adminspärren nekar när behörigheten inte kan avgöras
    check("F03: saknat användar-id via Ingress nekas", rm.admin_allowed({}, "172.30.32.2") is False)
    check("F03: anrop inifrån tillägget släpps igenom", rm.admin_allowed({}, "127.0.0.1") is True)
    orig, orig_user = rm.user_info, rm.app_user

    def boom(*a, **k):
        raise rm.WsError("Home Assistant svarar inte")
    rm.user_info, rm.app_user = boom, (lambda: "tvhub")  # testprocessen har ingen databas
    try:
        check("F03: uppslagsfel nekas (inte släpps igenom)", rm.admin_allowed({"X-Remote-User-Id": "u-admin"}, "172.30.32.2") is False)
    finally:
        rm.user_info, rm.app_user = orig, orig_user

    # F05: vuxeninnehåll på sidovägar
    s, ap = call("POST", f"{D}/device/play", {"type": "live", "id": "190"}, token=ta, mode="adult", profile=owner)
    check("vuxenkanal startar i vuxenläge", ap["status"] == "ok", ap)
    s, summ = call("GET", f"{D}/device/summary", token=tb)
    check("F05: summary visar aldrig vuxentitel", summ["playing"] and "18+" in json.dumps(summ["playing"])
          and ap["name"] not in json.dumps(summ["playing"]), summ["playing"])
    s, conf = call("POST", f"{D}/device/play", {"type": "live", "id": "102"}, token=tb)
    check("F05: konfliktmeddelandet nämner inte vuxentiteln", conf["status"] == "conflict"
          and ap["name"] not in json.dumps(conf) and conf["conflict"]["item_name"] == "18+", conf)
    s, ov = call("GET", f"{A}/api/overview")
    check("F05: adminöversikten visar inte vuxentiteln", ap["name"] not in json.dumps(ov["sessions"]), ov["sessions"])
    stop_all()
    now = int(time.time())
    s, rad = call("POST", f"{D}/device/recordings", {"stream_id": "190", "title": "HemligVuxenTitel", "start": now + 60,
                                                     "end": now + 600}, token=ta, mode="adult", profile=owner)
    s, allrec = call("GET", f"{D}/device/recordings?all=1", token=ta)
    mine = next(r for r in allrec if r["id"] == rad["id"])
    check("F05: schemat i vanligt läge maskerar vuxentitel och kanal", mine["title"] == "Inspelning" and mine["channel"] is None
          and "HemligVuxenTitel" not in json.dumps(allrec), mine)
    s, allrec = call("GET", f"{D}/device/recordings?all=1", token=ta, mode="adult", profile=owner)
    check("F05: … men inte i vuxenläge", next(r for r in allrec if r["id"] == rad["id"])["title"] == "HemligVuxenTitel")
    s, ok = call("POST", f"{D}/device/play", {"type": "live", "id": "190", "recording_id": rad["id"]}, token=ta)
    check("F05: planerad inspelning kan fortfarande starta (tjänsten)", ok["status"] == "ok", ok)
    stop_all()
    s, late = call("POST", f"{D}/device/recordings", {"stream_id": "101", "title": "Sen", "start": now + 7200,
                                                      "end": now + 9000}, token=ta)
    s, res = call("POST", f"{D}/device/play", {"type": "live", "id": "101", "recording_id": late["id"]}, token=ta)
    check("F05: inspelning långt före sin tid får ingen ström", res["status"] == "error", res)
    s, soon = call("POST", f"{D}/device/recordings", {"stream_id": "102", "title": "Snart", "start": now + 120,
                                                      "end": now + 900}, token=ta)
    call("POST", f"{D}/device/recordings/{soon['id']}/status", {"status": "done", "size_bytes": 5}, token=ta)
    s, res = call("POST", f"{D}/device/play", {"type": "live", "id": "102", "recording_id": soon["id"]}, token=ta)
    check("F05: klar inspelning får ingen ström", res["status"] == "error", res)
    for r in (rad, late, soon):
        call("DELETE", f"{D}/device/recordings/{r['id']}", token=ta)
    s, epg_n = call("GET", f"{D}/device/epg/190", token=ta)
    check("F05: programguide för vuxenkanal i vanligt läge är tom", s == 200 and epg_n == [], epg_n)
    s, en = call("GET", f"{D}/device/epg-now?ids=190,101", token=ta)
    check("F05: epg-now utelämnar vuxenkanalen", "190" not in en and "101" in en, list(en))
    for kind, iid in (("vod", "999999"), ("live", "888888")):
        s, res = call("POST", f"{D}/device/play", {"type": kind, "id": iid}, token=ta)
        check(f"F05: okänd {kind} spelas inte", res["status"] == "error", res)
    s, res = call("POST", f"{D}/device/play", {"type": "episode", "id": "9001", "series_id": "777777"}, token=ta)
    check("F05: avsnitt med okänd serie spelas inte", res["status"] == "error", res)

    # F06: två boxar som startar samtidigt får inte båda ström
    stop_all()
    out = _threads(2, lambda i: call("POST", f"{D}/device/play", {"type": "live", "id": "101" if i == 0 else "102"},
                                     token=ta if i == 0 else tb)[1]["status"])
    check("F06: samtidiga starter ger bara en ström", sorted(out) == ["conflict", "ok"], out)
    stop_all()

    # F07: parningskod och PIN vid parallella anrop
    s, code = call("POST", f"{A}/api/devices/pair-code")
    res = _threads(12, lambda i: call("POST", f"{D}/device/pair", {"code": code["code"], "name": f"Par{i}"})[0])
    check("F07: samma kod ger exakt en enhet vid parallella försök", res.count(200) == 1, res)
    s, devs = call("GET", f"{A}/api/devices")
    for d in devs:
        if d["name"].startswith("Par"):
            call("DELETE", f"{A}/api/devices/{d['id']}")
    lim = limits.AttemptLimiter(3, 60)
    r = [lim.reserve("k") for _ in range(5)]
    check("F07: försöksräknaren stoppar efter tillåtet antal", r[:3] == [None] * 3 and r[3] and r[4], r)
    lim.success("k")
    check("F07: rätt svar nollställer räknaren", lim.reserve("k") is None)
    call("PUT", f"{A}/api/adult-pin", {"pin": "4321"})
    res = _threads(8, lambda i: call("POST", f"{D}/device/adult-unlock", {"pin": "0000"}, token=ta, profile=owner)[1].get("error"))
    locked = sum(1 for e in res if e and "För många" in e)
    check("F07: parallella felaktiga PIN-försök spärras efter fem", locked >= 3, res)
    call("PUT", f"{A}/api/adult-pin", {"pin": ""})
    s, pt = call("POST", f"{A}/api/profiles", {"name": "PinTest"})
    call("PUT", f"{A}/api/profiles/{pt['id']}", {"pin": "2468"})
    codes = [call("POST", f"{D}/device/profile-check", {"profile_id": pt["id"], "pin": "1111"}, token=tb)[0] for _ in range(5)]
    s6, _ = call("POST", f"{D}/device/profile-check", {"profile_id": pt["id"], "pin": "1111"}, token=tb)
    s7, _ = call("POST", f"{D}/device/profile-check", {"profile_id": pt["id"], "pin": "2468"}, token=tb)
    check("F04: profil-PIN spärras efter fem fel (också rätt kod under spärren)", codes == [403] * 5 and s6 == 429 and s7 == 429, (codes, s6, s7))
    call("DELETE", f"{A}/api/profiles/{pt['id']}")

    # F09: bildproxyn hämtar inte interna adresser
    for u in (f"http://127.0.0.1:{ADMIN}/api/overview", "http://169.254.169.254/latest/meta-data", "http://[::1]:8123/x.png",
              "http://localhost:8080/x.png", "http://10.0.0.5/x.jpg", "http://192.168.1.1/x.jpg", "http://172.30.32.1:8123/x.png"):
        s, res = call("GET", f"{A}/api/img?u=" + urllib.parse.quote(u, safe=""))
        check(f"F09: bildproxyn nekar {u}", s == 400, (s, res))
    check("F09: källans egen adress är undantagen", limits.check_public(f"http://127.0.0.1:{FAKE_PORT}/x.png",
          allow_hosts={f"127.0.0.1:{FAKE_PORT}"}) is None)

    class Redir(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data")
            self.end_headers()

        def log_message(self, *a):
            pass
    rs = http.server.HTTPServer(("127.0.0.1", 0), Redir)
    threading.Thread(target=rs.serve_forever, daemon=True).start()
    try:
        limits.open_public(f"http://127.0.0.1:{rs.server_address[1]}/x", {}, 5, allow_hosts={f"127.0.0.1:{rs.server_address[1]}"})
        check("F09: omdirigering till intern adress stoppas", False, "följdes")
    except limits.BlockedAddress:
        check("F09: omdirigering till intern adress stoppas", True)
    finally:
        rs.shutdown()

    # F10: ogiltiga längder, JSON-krav, trådgräns
    sock = socket.create_connection(("127.0.0.1", ADMIN), timeout=5)
    sock.sendall(b"POST /api/profiles HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: -5\r\n\r\n")
    first = sock.recv(200).split(b"\r\n")[0]
    sock.close()
    check("F10: negativ Content-Length nekas", b" 400 " in first, first)
    req = urllib.request.Request(f"{A}/api/profiles", data=b'{"name": "Form"}', method="POST", headers={"Content-Type": "text/plain"})
    try:
        urllib.request.urlopen(req, timeout=5)
        code415 = 200
    except urllib.error.HTTPError as exc:
        code415 = exc.code
    check("F16: admin-API kräver JSON (inte formulär)", code415 == 415, code415)

    from hub.server import BoundedServer

    class Quiet(http.server.BaseHTTPRequestHandler):
        timeout = 8

    class One(BoundedServer):
        max_threads = 1
    bs = One(("127.0.0.1", 0), Quiet)
    threading.Thread(target=bs.serve_forever, daemon=True).start()
    held = socket.create_connection(bs.server_address, timeout=5)
    time.sleep(0.3)
    extra = socket.create_connection(bs.server_address, timeout=5)
    answer = extra.recv(100)
    check("F10: fler anslutningar än trådgränsen får 503", answer.startswith(b"HTTP/1.0 503"), answer)
    held.close()
    extra.close()
    bs.shutdown()

    # F11: samtidiga APK-nedladdningar
    tmp_dir = tempfile.mkdtemp(prefix="tvhub-apk-")
    old_dir, old_env = CFG["data_dir"], os.environ.get("APP_RELEASE_BASE")
    CFG["data_dir"] = tmp_dir
    os.environ["APP_RELEASE_BASE"] = f"http://127.0.0.1:{FAKE_PORT}/release"
    try:
        paths = _threads(6, lambda i: updates.ensure_apk(9999))
        left = sorted(os.listdir(tmp_dir))
        check("F11: samtidiga nedladdningar ger en komplett fil", len(set(paths)) == 1 and left == ["tvhub-9999.apk"]
              and os.path.getsize(paths[0]) > 100_000, (paths, left))
    finally:
        CFG["data_dir"] = old_dir
        if old_env is None:
            os.environ.pop("APP_RELEASE_BASE", None)
        else:
            os.environ["APP_RELEASE_BASE"] = old_env
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # F14: inga profilnamn till AI-tjänsten
    s, ex = call("GET", f"{A}/api/ai/export?viewer={child}&type=vod")
    check("F14: AI-underlaget innehåller inte profilens namn", s == 200 and "Testbarn" not in ex["text"], ex["text"][:200])

    # F17: profilradering tar bort sessionshistoriken
    s, rp = call("POST", f"{A}/api/profiles", {"name": "Raderas"})
    s, pl = call("POST", f"{D}/device/play", {"type": "live", "id": "101"}, token=ta, profile=rp["id"])
    call("POST", f"{D}/device/stop", {"session_id": pl["session_id"]}, token=ta)
    db = sqlite3.connect(os.path.join(DATA_DIR, "tvhub.db"))
    before = db.execute("SELECT COUNT(*) FROM sessions WHERE viewer_id = ?", (rp["id"],)).fetchone()[0]
    call("DELETE", f"{A}/api/profiles/{rp['id']}")
    after = db.execute("SELECT COUNT(*) FROM sessions WHERE viewer_id = ?", (rp["id"],)).fetchone()[0]
    db.close()
    check("F17: borttagen profil lämnar ingen sessionshistorik", before >= 1 and after == 0, (before, after))


def version_28(ta, tb, pa, pb):
    """2.8: källans svar vid fel i efterhandsuppspelning (Diagnostik)."""
    print("Version 2.8: diagnostik av efterhandsfel")
    s, data = call("GET", f"{A}/api/profiles")
    owner = {p["name"]: p for p in data["profiles"]}["Själv"]["id"]

    def stop_all():
        s, ov = call("GET", f"{A}/api/overview")
        for x in ov.get("sessions", []):
            tok = ta if x["device_id"] == pa["device_id"] else tb
            call("POST", f"{D}/device/stop", {"session_id": x["id"]}, token=tok)

    def probes():
        s, d = call("GET", f"{A}/api/diagnostics?limit=500")
        return [e for e in d["events"] if e["kind"] == "probe"]

    def wait_probe(count, wait=8):
        end = time.time() + wait
        while time.time() < end:
            found = probes()
            if len(found) >= count:
                return found
            time.sleep(0.3)
        return probes()

    stop_all()
    start = int(time.time()) - 7200
    urllib.request.urlopen(f"http://127.0.0.1:{FAKE_PORT}/__timeshift_html?on=1").read()
    try:
        before = len(probes())
        s, cu = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "start": start, "duration": 60}, token=ta, profile=owner)
        check("2.8: efterhandsström startar", cu.get("status") == "ok", cu)
        call("POST", f"{D}/device/report", {"kind": "error", "session_id": cu["session_id"],
             "detail": {"code": "ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED", "message": "Source error"}}, token=ta)
        found = wait_probe(before + 1)
        check("2.8: källans svar loggas efter containerfel", len(found) == before + 1, found[:1])
        detail = found[0]["detail"] if found else {}
        check("2.8: svaret visar status, typ och början", detail.get("status") == 200 and detail.get("content_type") == "text/html"
              and "arkivet" in detail.get("start", ""), detail)
        raw = json.dumps(found)
        check("2.8: inga kontouppgifter i loggen", "testpass" not in raw and "testuser" not in raw and "/timeshift/" not in raw, raw[:300])

        # Samma box igen direkt efter: begränsas (testet använder 3 s i stället för 60 s)
        call("POST", f"{D}/device/report", {"kind": "error", "session_id": cu["session_id"],
             "detail": {"code": "ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED", "message": "x"}}, token=ta)
        time.sleep(1.0)
        check("2.8: högst en undersökning per tidsfönster och box", len(probes()) == before + 1, len(probes()))

        # Andra fel än containerfel undersöks inte (även när fönstret har gått)
        time.sleep(3.0)
        call("POST", f"{D}/device/report", {"kind": "error", "session_id": cu["session_id"],
             "detail": {"code": "ERROR_CODE_IO_NETWORK_CONNECTION_FAILED", "message": "x"}}, token=ta)
        time.sleep(1.5)
        check("2.8: nätverksfel undersöks inte", len(probes()) == before + 1, len(probes()))

        # Vuxenkanal: ingen text ur svaret i loggen
        stop_all()
        s, ap = call("POST", f"{D}/device/play", {"type": "catchup", "id": "190", "start": start, "duration": 60},
                     token=ta, mode="adult", profile=owner)
        if ap.get("status") == "ok":
            call("POST", f"{D}/device/report", {"kind": "error", "session_id": ap["session_id"],
                 "detail": {"code": "ERROR_CODE_PARSING_CONTAINER_MALFORMED", "message": "x"}}, token=ta)
            found = wait_probe(before + 2)
            check("2.8: vuxenkanal: ingen text ur svaret i loggen", len(found) == before + 2 and "start" not in found[0]["detail"], found[:1])
        else:
            check("2.8: vuxenkanal startar i vuxenläge", False, ap)
    finally:
        urllib.request.urlopen(f"http://127.0.0.1:{FAKE_PORT}/__timeshift_html?on=0").read()
        stop_all()

    # Diagnostik: webbadminen har sök och filter
    s, html = call("GET", f"{A}/", raw=True)
    page = html.decode()
    check("2.8: Diagnostik har sökruta och filter", all(x in page for x in ("ev-search", "ev-box", "ev-kind", "ev-time", "ev-hide-start")))
    check("2.8: gesterna beskrivs inte i dokumentationen", all(
        "tre gånger" not in open(os.path.join(ROOT, f), encoding="utf-8").read() for f in ("README.md", os.path.join("tv_hub", "DOCS.md"))))


def version_29(ta, tb, pa, pb):
    """2.9: efterhandsadress, sorteringar, backup, avbrottsnotiser, boxöversikt."""
    print("Version 2.9: efterhandsadress, sorteringar, backup, notiser")
    s, data = call("GET", f"{A}/api/profiles")
    owner = {p["name"]: p for p in data["profiles"]}["Själv"]["id"]
    fake = f"http://127.0.0.1:{FAKE_PORT}"

    def stop_all():
        s, ov = call("GET", f"{A}/api/overview")
        for x in ov.get("sessions", []):
            tok = ta if x["device_id"] == pa["device_id"] else tb
            call("POST", f"{D}/device/stop", {"session_id": x["id"]}, token=tok)

    def ts_calls():
        return json.loads(urllib.request.urlopen(f"{fake}/__timeshift_calls").read())["calls"]

    def ts_mode(mode, **extra):
        query = "&".join([f"mode={mode}"] + [f"{k}={v}" for k, v in extra.items()])
        urllib.request.urlopen(f"{fake}/__timeshift_mode?{query}").read()

    start = int(time.time()) - 7200
    stop_all()
    try:
        # Leverantören ger tomt svar på sökvägsadressen men video på php-adressen
        ts_mode("php_only")
        s, cu = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "start": start, "duration": 60},
                     token=ta, profile=owner)
        check("2.9: efterhandsström startar", cu.get("status") == "ok", cu)
        check("2.9: hittar adressen som ger video", "timeshift.php" in cu.get("url", ""), cu.get("url"))
        forms = [c.split("|")[0] for c in ts_calls()]
        check("2.9: prövade sökvägsadressen först", forms[:1] == ["path"] and "php" in forms, forms)
        stop_all()
        ts_mode("php_only")
        s, cu2 = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "start": start, "duration": 60},
                      token=ta, profile=owner)
        check("2.9: sparad adress används utan ny sökning", "timeshift.php" in cu2.get("url", "") and ts_calls() == [], ts_calls())
        # Containerfel + inget video → sparad adress glöms
        def probe_count():
            s, d = call("GET", f"{A}/api/diagnostics?limit=500")
            return len([e for e in d["events"] if e["kind"] == "probe" and e["detail"].get("video") is False])
        base = probe_count()
        ts_mode("none")
        time.sleep(3.5)
        call("POST", f"{D}/device/report", {"kind": "error", "session_id": cu2["session_id"],
             "detail": {"code": "ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED", "message": "x"}}, token=ta)
        end = time.time() + 8
        gone = False
        while time.time() < end and not gone:
            time.sleep(0.5)
            gone = probe_count() > base
        check("2.9: tomt svar loggas som ingen video", gone)
        time.sleep(1.0)
        ts_mode("none")
        s, cu3 = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "start": start, "duration": 60},
                      token=ta, profile=owner)
        n = len(ts_calls())
        check("2.9: ny sökning görs efter att adressen glömts", n >= 2, n)
        check("2.9: sökningen misslyckas men spelning startas ändå", cu3.get("status") == "ok", cu3)
        stop_all()
        s, cu4 = call("POST", f"{D}/device/play", {"type": "catchup", "id": "101", "start": start, "duration": 60},
                      token=ta, profile=owner)
        check("2.9: misslyckad sökning upprepas inte direkt", len(ts_calls()) == n, (n, len(ts_calls())))
        s, d = call("GET", f"{A}/api/diagnostics?limit=500")
        ev = [e for e in d["events"] if e["kind"] == "timeshift"]
        check("2.9: sökningen loggas utan kontouppgifter", ev and "testpass" not in json.dumps(ev), ev[:1])
    finally:
        ts_mode("ok")
        stop_all()

    # Sorteringar
    def names(sort):
        s, r = call("GET", f"{D}/device/browse?type=vod&sort={sort}&limit=500", token=ta)
        return r["items"]
    for sort in ("name_desc", "added_asc", "year_asc", "poster"):
        s, r = call("GET", f"{D}/device/browse?type=vod&sort={sort}&limit=500", token=ta)
        check(f"2.9: sortering {sort} fungerar", s == 200 and len(r["items"]) > 3, (s, r))
    asc, desc = [i["name"].lower() for i in names("name")], [i["name"].lower() for i in names("name_desc")]
    check("2.9: Ö–A är A–Ö baklänges", asc == desc[::-1], (asc[:3], desc[:3]))
    items = names("poster")
    flags = [bool(i.get("icon")) for i in items]
    check("2.9: affisch först, sedan utan", flags == sorted(flags, reverse=True) and True in flags and False in flags, flags)

    # Boxöversikt
    s, devs = call("GET", f"{A}/api/devices")
    row = next(d for d in devs if d["id"] == pa["device_id"])
    check("2.9: boxöversikt visar uppkopplad", row.get("online") is True and "update_available" in row, row)

    # Avbrottsnotiser: inställningar och testfel utan notify-tjänst
    s, al = call("GET", f"{A}/api/alerts/settings")
    check("2.9: avbrottsnotiser har standardvärden", al == {"provider": True, "recording": True, "box_hours": 0}, al)
    s, bad = call("PUT", f"{A}/api/alerts/settings", {"box_hours": 5000})
    check("2.9: orimlig tid nekas", s == 400, bad)
    s, ok = call("PUT", f"{A}/api/alerts/settings", {"box_hours": 24, "recording": False})
    check("2.9: inställningarna sparas", ok["box_hours"] == 24 and ok["recording"] is False, ok)
    call("PUT", f"{A}/api/alerts/settings", {"box_hours": 0, "recording": True})
    s, bad = call("POST", f"{A}/api/alerts/test")
    check("2.9: testnotis skickas eller kräver notify-tjänst", s in (200, 400), (s, bad))

    # Backup
    s, prof = call("POST", f"{A}/api/profiles", {"name": "Backuptest", "emoji": "B", "pin": "4321"})
    s, bk = call("GET", f"{A}/api/backup")
    raw = json.dumps(bk)
    check("2.9: kopian har rätt format", bk.get("format") == "tvhub-settings" and bk.get("profiles"), list(bk))
    check("2.9: kopian innehåller inga koder, nycklar eller token", all(x not in raw for x in (
        "pin_hash", "pin_salt", "token_hash", "ai_key", "adult_pin", "testpass", "testuser", "remote_token")), raw[:200])
    check("2.9: profilen finns i kopian", any(p["name"] == "Backuptest" for p in bk["profiles"]))

    call("DELETE", f"{A}/api/profiles/{prof['id']}")
    s, r = call("POST", f"{A}/api/backup", bk)
    check("2.9: import lyckas", s == 200 and r["profiler"] >= 1, (s, r))
    s, data = call("GET", f"{A}/api/profiles")
    back = [p for p in data["profiles"] if p["name"] == "Backuptest"]
    check("2.9: profilen är tillbaka utan kod", len(back) == 1 and back[0]["has_pin"] is False, back)
    s, r2 = call("POST", f"{A}/api/backup", bk)
    s, data = call("GET", f"{A}/api/profiles")
    check("2.9: ny import skapar inga dubbletter", len([p for p in data["profiles"] if p["name"] == "Backuptest"]) == 1)
    s, bad = call("POST", f"{A}/api/backup", {"format": "annat"})
    check("2.9: främmande fil nekas", s == 400, bad)
    s, bad = call("POST", f"{A}/api/backup", {"format": "tvhub-settings", "version": 1, "profiles": "x"})
    check("2.9: trasig kopia nekas", s == 400, bad)
    call("DELETE", f"{A}/api/profiles/{back[0]['id']}")

    # Webbadminen
    s, html = call("GET", f"{A}/", raw=True)
    page = html.decode()
    check("2.9: webbadminen har backup och avbrottsnotiser", all(x in page for x in ("bk-export", "bk-file", "al-form", "al-test")))


def version_291(ta, tb, pa, pb):
    """2.9.1: Inte i Alla, Sport-flik, profilval och filmfilter via API:t."""
    print("Version 2.9.1: kategorival")
    s, cats = call("GET", f"{A}/api/categories?type=vod")
    check("2.9.1: kategorierna har skip_all och suggest_sport", cats and all("skip_all" in c and "suggest_sport" in c for c in cats), cats[:1])
    target = cats[0]["category_id"]
    s, items0 = call("GET", f"{D}/device/browse?type=vod&limit=500", token=ta)
    s, r = call("POST", f"{A}/api/categories/prefs", {"type": "vod", "changes": [{"category_id": target, "skip_all": True}]})
    s, items1 = call("GET", f"{D}/device/browse?type=vod&limit=500", token=ta)
    s, only = call("GET", f"{D}/device/browse?type=vod&skipped_only=1&limit=500", token=ta)
    s, incat = call("GET", f"{D}/device/browse?type=vod&category_id={target}&limit=500", token=ta)
    check("2.9.1: kategorin lämnar Alla", items1["total"] == items0["total"] - incat["total"] and incat["total"] > 0, (items0["total"], items1["total"], incat["total"]))
    check("2.9.1: Sport-fliken visar kategorin", only["total"] == incat["total"], (only["total"], incat["total"]))
    s, home = call("GET", f"{D}/device/home", token=ta)
    flagged = [c for c in home["categories"]["vod"] if c["category_id"] == target]
    check("2.9.1: appen får veta vilka kategorier som är utanför Alla", flagged and flagged[0]["skip_all"] is True, flagged)
    # Profilval
    s, data = call("GET", f"{A}/api/profiles")
    owner = {p["name"]: p for p in data["profiles"]}["Själv"]["id"]
    other = next(c["category_id"] for c in cats if c["category_id"] != target and c["count"] > 0 and not c["adult"])
    s, r = call("POST", f"{A}/api/categories/prefs", {"type": "vod", "profile": owner, "changes": [{"category_id": other, "hidden": True}]})
    s, home = call("GET", f"{D}/device/home", token=ta, profile=owner)
    check("2.9.1: profilen ser inte sin dolda kategori", other not in [c["category_id"] for c in home["categories"]["vod"]], home["categories"]["vod"])
    s, home2 = call("GET", f"{D}/device/home", token=ta)
    s, adm = call("GET", f"{A}/api/categories?type=vod&profile={owner}")
    check("2.9.1: admin ser profilens val", any(c["category_id"] == other and c["profile_hidden"] for c in adm), adm[:2])
    call("POST", f"{A}/api/categories/prefs", {"type": "vod", "profile": owner, "changes": [], "reset": True})
    s, home = call("GET", f"{D}/device/home", token=ta, profile=owner)
    check("2.9.1: återställning ger tillbaka kategorin", other in [c["category_id"] for c in home["categories"]["vod"]])
    # Filmfilter
    s, o = call("PUT", f"{A}/api/categories/options", {"hide_sport_titles": True, "new_requires_poster": True})
    check("2.9.1: filmfiltren sparas", o["hide_sport_titles"] and o["new_requires_poster"], o)
    s, o = call("PUT", f"{A}/api/categories/options", {"hide_sport_titles": False, "new_requires_poster": False})
    call("POST", f"{A}/api/categories/prefs", {"type": "vod", "changes": [{"category_id": target, "skip_all": None}]})
    s, items2 = call("GET", f"{D}/device/browse?type=vod&limit=500", token=ta)
    check("2.9.1: allt tillbaka när valet tas bort", items2["total"] == items0["total"], (items2["total"], items0["total"]))
    s, html = call("GET", f"{A}/", raw=True)
    page = html.decode()
    check("2.9.1: webbadminen har profilval och Inte i Alla", all(x in page for x in ("cat-profile", "data-act=\"skip\"", "cat-suggest", "opt-poster")))


def version_292():
    """2.9.2: favoriter för alla eller valda profiler."""
    print("Version 2.9.2: favoriter per profil")
    s, extra = call("POST", f"{A}/api/profiles", {"name": "Favtest"})
    s, pl = call("GET", f"{A}/api/favorites/profiles")
    byname = {p["name"]: p for p in pl["profiles"]}
    owner, kids, guest = byname["Själv"]["id"], extra["id"], byname["Gäst"]["id"]
    check("2.9.2: profillistan markerar ägaren", byname["Själv"]["owner"] is True and byname["Gäst"]["guest"] is True, pl)
    s, lib = call("GET", f"{A}/api/library/browse?type=vod&limit=10")
    ids = [i["id"] for i in lib["items"]][:2]
    x, y = ids[0], ids[1]
    for p in (owner, kids):
        for i in ids:
            call("POST", f"{A}/api/favorites", {"type": "vod", "id": i, "on": False, "profile": p})

    def favs(profile):
        s, r = call("GET", f"{A}/api/favorites?profile={profile}")
        return {i["id"]: i for i in r}
    call("POST", f"{A}/api/favorites", {"type": "vod", "id": x, "profile": owner})
    check("2.9.2: favorit för en vald profil", x in favs(owner) and x not in favs(kids) and favs(owner)[x]["profiles"] == [owner])
    s, r = call("POST", f"{A}/api/favorites/assign", {"type": "vod", "id": x, "profiles": "all"})
    check("2.9.2: visa för alla profiler (inte gäster)", x in favs(kids) and guest not in r["profiles"] and kids in r["profiles"], r)
    s, r = call("POST", f"{A}/api/favorites/assign", {"type": "vod", "id": x, "profiles": [kids]})
    check("2.9.2: bara valda profiler", x in favs(kids) and x not in favs(owner), (list(favs(owner)), r))
    call("POST", f"{A}/api/favorites/assign", {"type": "vod", "id": x, "profiles": [owner]})
    call("POST", f"{A}/api/favorites", {"type": "vod", "id": y, "profile": owner})
    s, r = call("POST", f"{A}/api/favorites/copy", {"from": owner, "to": "all"})
    check("2.9.2: kopiera alla favoriter", r["added"] >= 2 and x in favs(kids) and y in favs(kids), r)
    s, r2 = call("POST", f"{A}/api/favorites/copy", {"from": owner, "to": [kids]})
    check("2.9.2: kopiering skapar inga dubbletter", r2["added"] == 0, r2)
    s, bad = call("POST", f"{A}/api/favorites/copy", {"from": 99999, "to": "all"})
    check("2.9.2: okänd profil nekas", s in (400, 404), bad)
    s, lib2 = call("GET", f"{A}/api/library/browse?type=vod&adult=1&limit=5")
    adult_items = lib2.get("items") or []
    if adult_items:
        s, bad = call("POST", f"{A}/api/favorites/assign", {"type": "vod", "id": adult_items[0]["id"], "profiles": "all"})
        check("2.9.2: vuxenfavoriter delas inte härifrån", s == 400, (s, bad))
    for p in (owner, kids):
        for i in ids:
            call("POST", f"{A}/api/favorites", {"type": "vod", "id": i, "on": False, "profile": p})
    call("DELETE", f"{A}/api/profiles/{kids}")
    s, html = call("GET", f"{A}/", raw=True)
    check("2.9.2: webbadminen har profilval för favoriter", all(w in html.decode() for w in ("fav-profile", "fav-copy", "fav-share")))


def version_294(ta, tb, pa, pb):
    """2.9.4: markera sett/osett, 85 % räknas som sett, Chromecast i diagnostiken."""
    print("Version 2.9.4: sedda avsnitt och Chromecast")
    from hub import extras
    check("2.9.4: avsnitt räknas som sedda vid 85 %", extras.FINISHED_RATIO == 0.85)
    s, ser = call("GET", f"{D}/device/series/900", token=ta)
    ids = [e["id"] for sea in ser["seasons"] for e in sea["episodes"]][:2]
    s, r = call("POST", f"{D}/device/watched", {"series_id": "900", "ids": ids, "on": True}, token=ta)
    check("2.9.4: markera avsnitt som sedda", s == 200 and r["count"] == len(ids), (s, r))
    s, ser = call("GET", f"{D}/device/series/900", token=ta)
    check("2.9.4: sedda avsnitt syns i serien", all(i in ser["watched"] for i in ids), ser.get("watched"))
    s, r = call("POST", f"{D}/device/watched", {"series_id": "900", "ids": ids[:1], "on": False}, token=ta)
    s, ser = call("GET", f"{D}/device/series/900", token=ta)
    check("2.9.4: markera som osett", ids[0] not in ser["watched"] and ids[1] in ser["watched"], ser.get("watched"))
    call("POST", f"{D}/device/watched", {"series_id": "900", "ids": ids, "on": False}, token=ta)
    s, bad = call("POST", f"{D}/device/watched", {"series_id": "999999", "ids": ids, "on": True}, token=ta)
    check("2.9.4: okänd serie nekas", s == 404, (s, bad))
    s, r = call("POST", f"{D}/device/report", {"kind": "cast", "detail": {"device": "Vardagsrum", "ok": False, "ext": "mkv",
                "detail": "video/x-matroska: inget svar på 15 s"}}, token=ta)
    check("2.9.4: Chromecast-händelse tas emot", s == 200, (s, r))
    s, page = call("GET", f"{A}/", raw=True)
    check("2.9.4: diagnostiken visar Chromecast", "Chromecast" in page.decode() and 'case "cast"' in page.decode())


if __name__ == "__main__":
    main()
