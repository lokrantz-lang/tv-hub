#!/usr/bin/env python3
"""Simulerad Xtream Codes-server för tester.

Styr felbeteenden med GET /__mode?mode=ok|down_api|auth_fail|slow|vod_timeout|vod_empty
"""
import base64
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

USER, PASS = "testuser", "testpass"
STATE = {"mode": "ok", "active_cons": 0}

LIVE_CATS = [{"category_id": "1", "category_name": "SE | Sverige"},
             {"category_id": "2", "category_name": "Sport"}]
LIVE = [
    {"num": i, "name": name, "stream_id": 100 + i, "stream_icon": "", "epg_channel_id": f"ch{i}",
     "added": "1700000000", "category_id": cat}
    for i, (name, cat) in enumerate(
        [("SE: SVT1 HD", "1"), ("SE: SVT2 HD", "1"), ("SE: TV4 HD", "1"), ("SE: Kanal 5", "1"),
         ("C More Fotboll", "2"), ("Eurosport 1 HD", "2"), ("V Sport Premium", "2")], start=1)
]
VOD_CATS = [{"category_id": "10", "category_name": "Filmer 2026"}, {"category_id": "11", "category_name": "Svenska"}]
VOD = [
    {"stream_id": 500 + i, "name": name, "stream_icon": "", "rating": "7.1", "added": str(1700000000 + i),
     "category_id": cat, "container_extension": "mkv"}
    for i, (name, cat) in enumerate(
        [("Dune: Part Two (2024)", "10"), ("Oppenheimer (2023)", "10"), ("Jönssonligan (1981)", "11"),
         ("Så som i himmelen", "11"), ("Bamse och dunderklockan", "11")], start=1)
]
SERIES_CATS = [{"category_id": "20", "category_name": "Serier"}]
SERIES = [{"series_id": 900, "name": "Bonusfamiljen", "cover": "", "category_id": "20", "rating": "8",
           "last_modified": "1700000000"}]
SERIES_INFO = {
    "info": {"name": "Bonusfamiljen", "plot": "Familjedrama.", "cover": ""},
    "episodes": {
        "1": [{"id": "9001", "episode_num": 1, "title": "S01E01", "container_extension": "mp4",
               "info": {"duration": "00:45:00", "plot": "Första."}}],
        "2": [{"id": "9002", "episode_num": 1, "title": "S02E01", "container_extension": "mp4",
               "info": {"duration": "00:44:00"}}],
    },
}


def b64(text):
    return base64.b64encode(text.encode()).decode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        mode = STATE["mode"]

        if url.path == "/__mode":
            STATE["mode"] = q.get("mode", "ok")
            if "active_cons" in q:
                STATE["active_cons"] = int(q["active_cons"])
            return self.send(200, {"mode": STATE["mode"]})

        if url.path == "/":
            return self.send(404, b"not found", "text/plain")

        if url.path == "/player_api.php":
            if mode == "down_api":
                return self.send(503, b"down", "text/plain")
            if mode == "slow":
                time.sleep(1.5)
            if q.get("username") != USER or q.get("password") != PASS or mode == "auth_fail":
                return self.send(200, {"user_info": {"auth": 0}})
            action = q.get("action")
            if action is None:
                return self.send(200, {
                    "user_info": {"auth": 1, "status": "Active", "exp_date": str(int(time.time()) + 30 * 86400),
                                  "max_connections": "1", "active_cons": str(STATE["active_cons"]),
                                  "allowed_output_formats": ["m3u8", "ts"]},
                    "server_info": {"url": "127.0.0.1", "port": "8091"},
                })
            if action == "get_live_categories":
                return self.send(200, LIVE_CATS)
            if action == "get_live_streams":
                return self.send(200, LIVE)
            if action == "get_vod_categories":
                return self.send(200, VOD_CATS)
            if action == "get_vod_streams":
                if mode == "vod_timeout":
                    time.sleep(5)
                if mode == "vod_empty":
                    return self.send(200, [])
                return self.send(200, VOD)
            if action == "get_series_categories":
                return self.send(200, SERIES_CATS)
            if action == "get_series":
                return self.send(200, SERIES)
            if action == "get_series_info":
                return self.send(200, SERIES_INFO)
            if action == "get_short_epg":
                now = int(time.time())
                return self.send(200, {"epg_listings": [
                    {"title": b64("Rapport"), "description": b64("Nyheter"), "start_timestamp": str(now - 600),
                     "stop_timestamp": str(now + 1200)}]})
            return self.send(200, [])

        parts = url.path.strip("/").split("/")
        if len(parts) == 4 and parts[0] in ("live", "movie", "series"):
            if parts[1] != USER or parts[2] != PASS:
                return self.send(403, b"forbidden", "text/plain")
            # Film: så snabbt som möjligt i 3 MB. Live: 1 MB/s.
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.end_headers()
            chunk = b"\x47" + b"\x00" * 187
            block = chunk * 1000
            try:
                if parts[0] == "live":
                    for _ in range(40):
                        self.wfile.write(block * 5)
                        time.sleep(0.25)
                else:
                    for _ in range(200):
                        self.wfile.write(block * 5)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        return self.send(404, b"not found", "text/plain")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8091
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
