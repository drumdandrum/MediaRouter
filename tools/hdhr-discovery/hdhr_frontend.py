#!/usr/bin/env python3
"""Small HDHomeRun discovery and HTTP compatibility front end."""

import html
import json
import logging
import os
import re
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DISCOVER_PORT = 65001
DISCOVER_REQ = 0x0002
DISCOVER_RPY = 0x0003
TAG_DEVICE_TYPE = 0x01
TAG_DEVICE_ID = 0x02
TAG_TUNER_COUNT = 0x10
TAG_LINEUP_URL = 0x27
TAG_BASE_URL = 0x2A
TAG_DEVICE_AUTH = 0x2B
TYPE_TUNER = 0x00000001
WILDCARD = 0xFFFFFFFF

PUBLIC_HOST = os.getenv("HDHR_PUBLIC_HOST", "192.168.2.189")
HTTP_PORT = int(os.getenv("HDHR_HTTP_PORT", "80"))
UPSTREAM = os.getenv("HDHR_UPSTREAM", "http://127.0.0.1:5004").rstrip("/")
M3U_FILE = os.getenv("HDHR_M3U_FILE", "/media-router/live/live.m3u")
M3U_URL = os.getenv("HDHR_M3U_URL", "http://192.168.2.189:8090/live.m3u")
DEVICE_ID = int(os.getenv("HDHR_DEVICE_ID", "A1B2C3D2"), 16)
TUNER_COUNT = int(os.getenv("HDHR_TUNER_COUNT", "4"))
FRIENDLY_NAME = os.getenv("HDHR_FRIENDLY_NAME", "NextPVR HDHomeRun Shim")
DEVICE_AUTH = os.getenv("HDHR_DEVICE_AUTH", "mediarouter")
LINEUP_CACHE_SECONDS = int(os.getenv("HDHR_LINEUP_CACHE_SECONDS", "30"))
LINEUP_CACHE = {"loaded": 0.0, "items": []}
EXTINF_RE = re.compile(r"#EXTINF:(?P<meta>[^,]*),(?P<name>.*)$")
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def valid_device_id(device_id):
    lookup = (0xA, 0x5, 0xF, 0x6, 0x7, 0xC, 0x1, 0xB,
              0x9, 0x2, 0x8, 0xD, 0x4, 0x3, 0xE, 0x0)
    checksum = 0
    for shift in (28, 20, 12, 4):
        checksum ^= lookup[(device_id >> shift) & 0x0F]
    for shift in (24, 16, 8, 0):
        checksum ^= (device_id >> shift) & 0x0F
    return checksum == 0


def encode_length(length):
    if length <= 127:
        return bytes((length,))
    if length > 0x3FFF:
        raise ValueError("TLV value is too large")
    return bytes(((length & 0x7F) | 0x80, length >> 7))


def tlv(tag, value):
    return bytes((tag,)) + encode_length(len(value)) + value


def frame(packet_type, payload):
    body = struct.pack(">HH", packet_type, len(payload)) + payload
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def parse_frame(data):
    if len(data) < 8:
        raise ValueError("short frame")
    packet_type, length = struct.unpack(">HH", data[:4])
    if len(data) != length + 8:
        raise ValueError("bad frame length")
    expected = struct.unpack("<I", data[-4:])[0]
    if (zlib.crc32(data[:-4]) & 0xFFFFFFFF) != expected:
        raise ValueError("bad frame CRC")
    values = {}
    pos, end = 4, 4 + length
    while pos < end:
        tag = data[pos]
        pos += 1
        size = data[pos]
        pos += 1
        if size & 0x80:
            size = (size & 0x7F) | (data[pos] << 7)
            pos += 1
        if pos + size > end:
            raise ValueError("bad TLV length")
        values.setdefault(tag, []).append(data[pos:pos + size])
        pos += size
    return packet_type, values


def base_url():
    return f"http://{PUBLIC_HOST}" if HTTP_PORT == 80 else f"http://{PUBLIC_HOST}:{HTTP_PORT}"


def parse_m3u(text):
    items = []
    seen_urls = set()
    pending = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXTINF:"):
            match = EXTINF_RE.match(line)
            if not match:
                pending = None
                continue
            attrs = dict(ATTR_RE.findall(match.group("meta")))
            pending = {
                "GuideNumber": attrs.get("tvg-chno") or attrs.get("channel-number") or str(len(items) + 1),
                "GuideName": html.unescape(attrs.get("tvg-name") or match.group("name").strip()),
            }
        elif line and not line.startswith("#") and pending:
            if line.startswith(("http://", "https://")) and line not in seen_urls:
                pending["URL"] = line
                items.append(pending)
                seen_urls.add(line)
            pending = None
    return items


def load_lineup():
    now = time.monotonic()
    if LINEUP_CACHE["items"] and now - LINEUP_CACHE["loaded"] < LINEUP_CACHE_SECONDS:
        return LINEUP_CACHE["items"]
    try:
        with open(M3U_FILE, "r", encoding="utf-8-sig", errors="replace") as source:
            text = source.read()
    except OSError:
        with urllib.request.urlopen(M3U_URL, timeout=20) as source:
            text = source.read().decode("utf-8-sig", "replace")
    items = parse_m3u(text)
    if not items:
        raise ValueError("MediaRouter playlist contains no channels")
    LINEUP_CACHE.update(loaded=now, items=items)
    return items


def discovery_reply():
    base = base_url()
    payload = b"".join((
        tlv(TAG_DEVICE_TYPE, struct.pack(">I", TYPE_TUNER)),
        tlv(TAG_DEVICE_ID, struct.pack(">I", DEVICE_ID)),
        tlv(TAG_TUNER_COUNT, bytes((TUNER_COUNT,))),
        tlv(TAG_BASE_URL, base.encode() + b"\0"),
        tlv(TAG_LINEUP_URL, f"{base}/lineup.json".encode() + b"\0"),
        tlv(TAG_DEVICE_AUTH, DEVICE_AUTH.encode() + b"\0"),
    ))
    return frame(DISCOVER_RPY, payload)


def matches_request(values):
    requested_types = [struct.unpack(">I", v)[0] for v in values.get(TAG_DEVICE_TYPE, []) if len(v) == 4]
    requested_ids = [struct.unpack(">I", v)[0] for v in values.get(TAG_DEVICE_ID, []) if len(v) == 4]
    type_ok = not requested_types or TYPE_TUNER in requested_types or WILDCARD in requested_types
    id_ok = not requested_ids or DEVICE_ID in requested_ids or WILDCARD in requested_ids
    return type_ok and id_ok


def serve_discovery():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", DISCOVER_PORT))
    reply = discovery_reply()
    logging.info("HDHomeRun discovery listening on UDP %s", DISCOVER_PORT)
    while True:
        data, peer = sock.recvfrom(2048)
        try:
            packet_type, values = parse_frame(data)
            if packet_type == DISCOVER_REQ and matches_request(values):
                sock.sendto(reply, peer)
        except (ValueError, IndexError, struct.error):
            logging.debug("ignored invalid discovery packet from %s", peer)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, value):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _lineup(self):
        try:
            return load_lineup()
        except (OSError, ValueError, urllib.error.URLError) as exc:
            logging.error("unable to load MediaRouter lineup: %s", exc)
            self.send_error(502, "MediaRouter lineup unavailable")
            return None

    def _proxy(self):
        url = UPSTREAM + self.path
        body = None
        if self.command == "POST":
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        req = urllib.request.Request(url, data=body, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=30) as upstream:
                content = upstream.read()
                self.send_response(upstream.status)
                self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/octet-stream"))
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
        except urllib.error.HTTPError as exc:
            content = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except OSError:
            self.send_error(502, "HDHomeRun shim unavailable")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/discover.json":
            base = base_url()
            self._json({
                "FriendlyName": FRIENDLY_NAME,
                "Manufacturer": "Silicondust",
                "ModelNumber": "HDHR3-US",
                "FirmwareName": "hdhomerun3_atsc",
                "FirmwareVersion": "20260905",
                "TunerCount": TUNER_COUNT,
                "DeviceID": f"{DEVICE_ID:08X}",
                "DeviceAuth": DEVICE_AUTH,
                "BaseURL": base,
                "LineupURL": f"{base}/lineup.json",
            })
            return
        if path == "/lineup_status.json":
            self._json({"ScanInProgress": 0, "ScanPossible": 0, "Source": "Cable", "SourceList": ["Cable"]})
            return
        if path == "/lineup.json":
            items = self._lineup()
            if items is not None:
                self._json(items)
            return
        if path == "/lineup.m3u":
            items = self._lineup()
            if items is None:
                return
            lines = ["#EXTM3U"]
            for item in items:
                name = item["GuideName"].replace("\r", " ").replace("\n", " ")
                lines.extend((f'#EXTINF:-1 tvg-chno="{item["GuideNumber"]}",{name}', item["URL"]))
            body = ("\n".join(lines) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-mpegURL")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/auto/"):
            self.send_response(307)
            self.send_header("Location", UPSTREAM + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._proxy()

    def do_POST(self):
        self._proxy()

    def log_message(self, fmt, *args):
        logging.info("http %s - %s", self.client_address[0], fmt % args)


def main():
    if not valid_device_id(DEVICE_ID):
        raise SystemExit(f"invalid HDHomeRun device ID: {DEVICE_ID:08X}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    thread = threading.Thread(target=serve_discovery, name="hdhr-discovery", daemon=True)
    thread.start()
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    logging.info("HDHomeRun HTTP front end listening on port %s", HTTP_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
