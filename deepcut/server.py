#!/usr/bin/env python3
"""
Deep Cut's web UI and JSON API.

Narrow on purpose: it can list candidates, start a search, mark a candidate
rejected, or approve one -- which downloads exactly that file and imports it
as the film it was found for. It cannot delete anything from the library.
"""
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import downloader, scanner, store
from .config import WEB_PORT

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")
log = logging.getLogger("deepcut")


def summary():
    movies = store.overview()
    pending = sum(1 for m in movies if not m["dismissed"] for c in m["candidates"] if c["state"] == "new")
    films_waiting = sum(1 for m in movies if not m["dismissed"] and any(c["state"] == "new" for c in m["candidates"]))
    imported = sum(1 for m in movies for c in m["candidates"] if c["state"] == "imported")
    active = sum(1 for m in movies for c in m["candidates"] if c["state"] in ("queued", "downloading"))
    return {
        "to_review": pending,
        "films_waiting": films_waiting,
        "missing_tracked": len(movies),
        "downloading": active,
        "imported": imported,
        "last_scan": store.kv_get("last_scan"),
        "scan": scanner.status,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._file(os.path.join(WEB, "index.html"), "text/html; charset=utf-8")
        if p == "/logo.svg":
            return self._file(os.path.join(WEB, "logo.svg"), "image/svg+xml")
        if p == "/api/films":
            return self._json(store.overview())
        if p == "/api/summary":
            return self._json(summary())
        if p == "/health":
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
        except ValueError:
            body = {}
        if p == "/api/scan":
            if scanner.status["running"]:
                return self._json({"error": "a scan is already running"}, 409)
            mid = body.get("movieId")
            scanner.start_scan(force=True, only_id=int(mid) if mid else None)
            return self._json({"started": True})
        m = re.fullmatch(r"/api/candidate/(\d+)/(approve|reject|restore)", p)
        if m:
            cid, action = int(m.group(1)), m.group(2)
            c = store.candidate(cid)
            if not c:
                return self._json({"error": "no such candidate"}, 404)
            if action == "approve":
                if c["state"] not in ("new", "failed"):
                    return self._json({"error": f"candidate is {c['state']}"}, 409)
                downloader.enqueue(cid)
            elif action == "reject":
                store.set_state(cid, "rejected")
            else:
                store.set_state(cid, "new")
            return self._json(store.candidate(cid))
        m = re.fullmatch(r"/api/film/(\d+)/(dismiss|undismiss)", p)
        if m:
            store.set_dismissed(int(m.group(1)), m.group(2) == "dismiss")
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    store.init()
    downloader.start()
    threading.Thread(target=scanner.scheduler, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler)
    log.info("Deep Cut listening on :%d", WEB_PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
