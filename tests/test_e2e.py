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
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "tv_hub", "app", "main.py")
FAKE = os.path.join(ROOT, "tests", "fake_xtream.py")
FAKE_PORT, ADMIN, DEVICE = 18091, 18080, 18099
A = f"http://127.0.0.1:{ADMIN}"
D = f"http://127.0.0.1:{DEVICE}"
FAILS = []


def call(method, url, body=None, token=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
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
               ADMIN_ALLOWED_IPS="127.0.0.1", NO_PROXY="*", no_proxy="*")
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
        check("räknare", ov["catalog"]["live"]["items"] == 7 and ov["catalog"]["vod"]["items"] == 5
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
        stall = [f for f in diag["diagnosis"]["findings"] if "hackar" in f["title"]]
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
        check("gamla VOD-listan behålls", ov["catalog"]["vod"]["items"] == 5, ov["catalog"])
        fake_mode("vod_empty")
        call("POST", f"{A}/api/catalog/sync")
        time.sleep(1)
        wait_for(lambda: _sync_done(), timeout=30)
        s, ov = call("GET", f"{A}/api/overview")
        check("tom lista skriver inte över", ov["catalog"]["vod"]["items"] == 5, ov["catalog"])

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
