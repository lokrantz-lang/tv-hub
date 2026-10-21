#!/usr/bin/env python3
"""Låtsas-DLNA-server (som Synologys Media Server) för test av appens DLNA-klient.
Användning: fake_dlna.py <http-port> <ssdp-port>"""
import html
import re
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTTP_PORT, SSDP_PORT = int(sys.argv[1]), int(sys.argv[2])
BASE = f"http://127.0.0.1:{HTTP_PORT}"

DESC = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
 <specVersion><major>1</major><minor>0</minor></specVersion>
 <device>
  <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
  <friendlyName>DiskStation</friendlyName>
  <UDN>uuid:4d696e69-444c-164e-9d41-001132abcdef</UDN>
  <serviceList>
   <service><serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType><controlURL>/cm/control</controlURL></service>
   <service><serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType><controlURL>/cd/control</controlURL></service>
  </serviceList>
 </device>
</root>"""

def item_video(i, title):
    return (f'<item id="v{i}" parentID="20" restricted="1"><dc:title>{html.escape(title)}</dc:title>'
            f'<upnp:class>object.item.videoItem.movie</upnp:class><dc:date>2019-06-0{i % 9 + 1}</dc:date>'
            f'<upnp:albumArtURI dlna:profileID="JPEG_TN">/thumb/v{i}.jpg</upnp:albumArtURI>'
            f'<res protocolInfo="http-get:*:video/x-matroska:*" size="123456" duration="1:23:45.500" resolution="1920x1080">{BASE}/v/{i}.mkv</res>'
            f'<res protocolInfo="http-get:*:text/srt:*">{BASE}/sub/{i}.srt</res></item>')

def item_image(i):
    return (f'<item id="p{i}" parentID="30" restricted="1"><dc:title>IMG_{1000 + i}</dc:title>'
            f'<upnp:class>object.item.imageItem.photo</upnp:class>'
            f'<res protocolInfo="http-get:*:image/jpeg:DLNA.ORG_PN=JPEG_LRG" resolution="4032x3024">{BASE}/p/{i}.jpg</res>'
            f'<res protocolInfo="http-get:*:image/jpeg:DLNA.ORG_PN=JPEG_SM" resolution="640x480">{BASE}/p/{i}_sm.jpg</res>'
            f'<res protocolInfo="http-get:*:image/jpeg:DLNA.ORG_PN=JPEG_MED" resolution="1920x1440">{BASE}/p/{i}_med.jpg</res>'
            f'<res protocolInfo="http-get:*:image/jpeg:DLNA.ORG_PN=JPEG_TN" resolution="160x120">{BASE}/p/{i}_tn.jpg</res></item>')

TREE = {
    "0": [('<container id="1" parentID="0" childCount="2" restricted="1"><dc:title>Video</dc:title>'
           '<upnp:class>object.container.storageFolder</upnp:class></container>'),
          ('<container id="2" parentID="0" childCount="1" restricted="1"><dc:title>Foto</dc:title>'
           '<upnp:class>object.container.storageFolder</upnp:class></container>')],
    "1": [('<container id="20" parentID="1" childCount="250" restricted="1"><dc:title>Barnfilmer &amp; tecknat</dc:title>'
           '<upnp:class>object.container.storageFolder</upnp:class></container>')],
    "20": [item_video(i, f"Film {i:03d} (2019)") for i in range(250)],
    "2": [item_image(i) for i in range(3)],
}

def didl(objects):
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
            + "".join(objects) + '</DIDL-Lite>')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/xml"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/desc/device.xml":
            return self.send(200, DESC)
        self.send(404, "nej")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.path != "/cd/control" or "#Browse" not in (self.headers.get("SOAPACTION") or ""):
            return self.send(500, "fel")
        oid = re.search(r"<ObjectID>(.*?)</ObjectID>", body).group(1)
        start = int(re.search(r"<StartingIndex>(\d+)</StartingIndex>", body).group(1))
        count = int(re.search(r"<RequestedCount>(\d+)</RequestedCount>", body).group(1))
        objs = TREE.get(html.unescape(oid), [])
        page = objs[start:start + count]
        result = html.escape(didl(page))
        self.send(200, f"""<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
<u:BrowseResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1"><Result>{result}</Result>
<NumberReturned>{len(page)}</NumberReturned><TotalMatches>{len(objs)}</TotalMatches><UpdateID>1</UpdateID></u:BrowseResponse></s:Body></s:Envelope>""")


def ssdp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", SSDP_PORT))
    while True:
        data, addr = sock.recvfrom(4096)
        text = data.decode(errors="replace")
        if "M-SEARCH" in text and "ContentDirectory" in text:
            reply = ("HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\nEXT:\r\n"
                     f"LOCATION: {BASE}/desc/device.xml\r\nSERVER: Linux UPnP/1.0 DLNA\r\n"
                     "ST: urn:schemas-upnp-org:service:ContentDirectory:1\r\nUSN: uuid:x::urn:schemas-upnp-org:service:ContentDirectory:1\r\n\r\n")
            sock.sendto(reply.encode(), addr)


threading.Thread(target=ssdp, daemon=True).start()
ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler).serve_forever()
