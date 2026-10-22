#!/usr/bin/env python3
"""Startar simulerad källa + TV Hub med demodata (för skärmbilder och manuell test).
Admin: http://127.0.0.1:28080  Boxar: http://127.0.0.1:28099"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A, D = "http://127.0.0.1:28080", "http://127.0.0.1:28099"


def call(method, url, body=None, token=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read() or b"null")


env = dict(os.environ, DATA_DIR=tempfile.mkdtemp(prefix="tvhub-demo-"), ADMIN_PORT="28080", DEVICE_PORT="28099",
           CHECK_INTERVAL="5", NET_CHECK_INTERVAL="5", REFERENCE_HOSTS="127.0.0.1:28091",
           OPTIONS_PATH="/nonexistent", ADMIN_ALLOWED_IPS="127.0.0.1", NO_PROXY="*", no_proxy="*",
           INFO_START_DELAY="1", INFO_PAUSE="0.3", GUIDE_CACHE_S="0", SPORT_SCAN_DELAY="5", SPORT_SCAN_PAUSE="0", APP_RELEASE_BASE="http://127.0.0.1:28091/release")
subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "fake_xtream.py"), "28091"])
subprocess.Popen([sys.executable, os.path.join(ROOT, "tv_hub", "app", "main.py")], env=env)
time.sleep(2)
call("POST", f"{A}/api/portals", {"name": "Toucan", "url": "http://127.0.0.1:28091", "username": "testuser",
                                  "password": "testpass", "priority": 1})
call("POST", f"{A}/api/portals", {"name": "Toucan reserv", "url": "http://127.0.0.1:28092", "username": "testuser",
                                  "password": "testpass", "priority": 2})
call("POST", f"{A}/api/catalog/sync")
time.sleep(3)
for t, i in (("live", "101"), ("live", "105"), ("vod", "501")):
    call("POST", f"{A}/api/favorites", {"type": t, "id": i, "on": True})
g = call("POST", f"{A}/api/groups", {"name": "Barnen"})
for i in ("505", "503"):
    call("POST", f"{A}/api/groups/{g['id']}/items", {"type": "vod", "id": i})
call("POST", f"{A}/api/groups/{g['id']}/items", {"type": "series", "id": "900"})
code = call("POST", f"{A}/api/devices/pair-code")["code"]
z10 = call("POST", f"{D}/device/pair", {"code": code, "name": "Vardagsrum (Z10 Pro)", "model": "Formuler Z10 Pro",
                                        "app_version": "1.0.0"})["token"]
code = call("POST", f"{A}/api/devices/pair-code")["code"]
alpha = call("POST", f"{D}/device/pair", {"code": code, "name": "Sovrum (Alpha)", "model": "Formuler Z Alpha",
                                          "app_version": "1.0.0"})["token"]
play = call("POST", f"{D}/device/play", {"type": "live", "id": "105"}, token=z10)
call("POST", f"{D}/device/heartbeat", {"session_id": play["session_id"],
                                       "metrics": {"bandwidth_bps": 14.2e6, "stalls": 2}}, token=z10)
call("POST", f"{D}/device/report", {"kind": "speedtest", "detail": {"mbps": 94.0}}, token=z10)
call("POST", f"{D}/device/report", {"kind": "stall", "session_id": play["session_id"],
                                    "detail": {"ms": 2400, "bandwidth_bps": 5.1e6, "bitrate_bps": 6.0e6,
                                               "buffered_ms": 0}}, token=z10)
call("POST", f"{A}/api/categories/prefs", {"type": "live", "changes": [{"category_id": "3", "hidden": True}]})
call("POST", f"{A}/api/watches", {"query": "Dune", "type": "vod"})
call("POST", f"{D}/device/filters", {"name": "Korta bra filmer", "type": "vod", "params": {"max_duration": 120, "min_rating": 6}}, token=z10)
now = int(time.time())
r1 = call("POST", f"{D}/device/recordings", {"stream_id": "101", "title": "Rapport", "start": now - 600, "end": now + 1200}, token=z10)
rp = call("POST", f"{D}/device/play", {"type": "live", "id": "101", "recording_id": r1["id"]}, token=z10)
call("POST", f"{D}/device/recordings/{r1['id']}/status", {"status": "recording", "size_bytes": 412000000}, token=z10)
r2 = call("POST", f"{D}/device/recordings", {"stream_id": "101", "title": "Sportnytt", "start": now - 7200, "end": now - 5400}, token=z10)
call("POST", f"{D}/device/recordings/{r2['id']}/status", {"status": "done", "size_bytes": 1830000000}, token=z10)
call("POST", f"{D}/device/recordings", {"stream_id": "102", "title": "Kvällsfilm", "start": now + 7200, "end": now + 13800}, token=alpha)
r4 = call("POST", f"{D}/device/recordings", {"stream_id": "102", "title": "Nyheterna", "start": now + 300, "end": now + 900}, token=alpha)
urllib.request.urlopen("http://127.0.0.1:28091/__mode?mode=ok&news=1&sport=1").read()
henrik = call("GET", f"{A}/api/profiles")["profiles"][0]["id"]
call("POST", f"{A}/api/my-programs/suggest", {"profile_id": henrik})
time.sleep(1)
call("POST", f"{A}/api/sports/scan")
print("demo redo", flush=True)
while True:
    time.sleep(5)
    call("POST", f"{D}/device/heartbeat", {"session_id": play["session_id"],
                                           "metrics": {"bandwidth_bps": 14.2e6, "stalls": 2}}, token=z10)
    call("POST", f"{D}/device/heartbeat", {"session_id": rp["session_id"], "metrics": {}}, token=z10)
