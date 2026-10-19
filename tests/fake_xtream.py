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

def _poster(path, w=60, h=90):
    """En enfärgad PNG som affisch (färgen beror på adressen)."""
    import struct
    import zlib
    seed = sum(path.encode())
    rgb = bytes(((seed * 37) % 200 + 30, (seed * 91) % 200 + 30, (seed * 53) % 200 + 30))
    raw = b"".join(b"\x00" + rgb * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


LIVE_CATS = [{"category_id": "1", "category_name": "SE | Sverige"},
             {"category_id": "2", "category_name": "Sport"},
             {"category_id": "3", "category_name": "XXX | For Adults"}]
LIVE = [
    {"num": i, "name": name, "stream_id": 100 + i, "stream_icon": "", "epg_channel_id": f"ch{i}",
     "added": "1700000000", "category_id": cat, "tv_archive": 1 if i == 1 else 0,
     "tv_archive_duration": 3 if i == 1 else 0}
    for i, (name, cat) in enumerate(
        [("SE: SVT1 HD", "1"), ("SE: SVT2 HD", "1"), ("SE: TV4 HD", "1"), ("SE: Kanal 5", "1"),
         ("C More Fotboll", "2"), ("Eurosport 1 HD", "2"), ("V Sport Premium", "2")], start=1)
] + [{"num": 90, "name": "Hot Channel HD", "stream_id": 190, "stream_icon": "", "epg_channel_id": "",
      "added": "1700000000", "category_id": "3"}]
VOD_CATS = [{"category_id": "10", "category_name": "Filmer 2026"}, {"category_id": "11", "category_name": "Svenska"}]
VOD = [
    {"stream_id": 500 + i, "name": name, "stream_icon": f"__BASE__/img/v{500 + i}.png", "rating": "7.1", "added": str(1700000000 + i),
     "category_id": cat, "container_extension": "mkv"}
    for i, (name, cat) in enumerate(
        [("Dune: Part Two (2024)", "10"), ("Oppenheimer (2023)", "10"), ("Jönssonligan (1981)", "11"),
         ("Så som i himmelen", "11"), ("Bamse och dunderklockan", "11")], start=1)
] + [{"stream_id": 599, "name": "Nattfilm", "stream_icon": "", "rating": "", "added": "1700000099",
      "category_id": "10", "container_extension": "mp4", "is_adult": "1"}]
SERIES_CATS = [{"category_id": "20", "category_name": "Serier"}]
SERIES = [{"series_id": 900, "name": "Bonusfamiljen", "cover": "__BASE__/img/s900.png", "category_id": "20", "rating": "8",
           "last_modified": "1700000000", "plot": "Familjedrama i Stockholm.", "genre": "Drama, Komedi",
           "releaseDate": "2017-09-11", "cast": "Vera Vitali", "episode_run_time": "45"}]
VOD_INFO = {  # längd i sekunder och genre per film
    "501": (9960, "Science Fiction, Äventyr", "2024-02-27", "8.1"),
    "502": (10800, "Drama, Historia", "2023-07-19", "8.3"),
    "503": (5640, "Komedi, Kriminal", "1981-12-18", "6.9"),
    "504": (7980, "Drama, Musik", "2004-08-27", "7.4"),
    "505": (4500, "Animation, Barn", "1990-01-01", "5.9"),
    "599": (4200, "Vuxen", "2020-01-01", ""),
}
APK = b"PK" + b"\x00" * 200_000
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
        if isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload).replace("__BASE__", f"http://{self.headers.get('Host', '127.0.0.1')}").encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        mode = STATE["mode"]

        if url.path == "/__last_timeshift":
            return self.send(200, {"path": STATE.get("last_timeshift")})
        if url.path == "/__mode":
            STATE["mode"] = q.get("mode", "ok")
            if "extra" in q:
                STATE["extra"] = q["extra"] == "1"
            if "active_cons" in q:
                STATE["active_cons"] = int(q["active_cons"])
            return self.send(200, {"mode": STATE["mode"]})

        if url.path == "/":
            return self.send(404, b"not found", "text/plain")
        if url.path.startswith("/img/"):
            return self.send(200, _poster(url.path), "image/png")

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
                if STATE.get("extra"):
                    return self.send(200, VOD + [{"stream_id": 520, "name": "Dune: Part Three (2026)",
                                                  "stream_icon": "", "rating": "", "added": "1800000000",
                                                  "category_id": "10", "container_extension": "mkv"}])
                return self.send(200, VOD)
            if action == "get_series_categories":
                return self.send(200, SERIES_CATS)
            if action == "get_series":
                return self.send(200, SERIES)
            if action == "get_series_info":
                return self.send(200, SERIES_INFO)
            if action == "get_vod_info":
                vid = q.get("vod_id", "")
                if vid not in VOD_INFO:
                    return self.send(200, {"info": [], "movie_data": {}})
                secs, genre, release, rating = VOD_INFO[vid]
                return self.send(200, {"info": {"duration_secs": secs, "genre": genre, "releasedate": release,
                                                "plot": f"Handling för film {vid}.", "cast": "Skådespelare A",
                                                "director": "Regissör B", "rating": rating,
                                                "movie_image": "", "backdrop_path": ["http://x/b.jpg"]},
                                       "movie_data": {"stream_id": int(vid)}})
            if action == "get_simple_data_table":
                now = int(time.time())
                base = now - now % 3600
                listings = []
                for k in range(-4, 4):
                    start = base + k * 3600
                    listings.append({"title": b64(f"Program {k}"), "description": b64(""),
                                     "start_timestamp": str(start), "stop_timestamp": str(start + 3600),
                                     "has_archive": "1" if start + 3600 <= now else "0"})
                return self.send(200, {"epg_listings": listings})
            if action == "get_short_epg":
                now = int(time.time())
                return self.send(200, {"epg_listings": [
                    {"title": b64("Rapport"), "description": b64("Nyheter"), "start_timestamp": str(now - 600),
                     "stop_timestamp": str(now + 1200)}]})
            return self.send(200, [])

        if url.path == "/release/version.json":
            return self.send(200, {"versionCode": 42, "versionName": "1.2.42"})
        if url.path == "/release/tvhub.apk":
            return self.send(200, APK, "application/vnd.android.package-archive")

        parts = url.path.strip("/").split("/")
        if len(parts) == 6 and parts[0] == "timeshift":
            if parts[1] != USER or parts[2] != PASS:
                return self.send(403, b"forbidden", "text/plain")
            STATE["last_timeshift"] = url.path
            return self.send(200, b"\x47" * 188 * 100, "video/mp2t")
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
