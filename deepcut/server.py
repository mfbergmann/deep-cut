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
from urllib.parse import unquote, urlparse

from . import downloader, inbox, radarr, scanner, store
from .config import INBOX_SHARE, VIMEO_COOKIES, WEB_PORT
from .sources import vimeo

MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
        ".mkv": "video/webm", ".ogv": "video/ogg"}  # browsers play most MKVs when told webm

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")
log = logging.getLogger("deepcut")


def summary():
    movies = store.overview()
    # A film with an imported candidate is done, whatever else is listed for it.
    done = {m["radarr_id"] for m in movies if any(c["state"] == "imported" for c in m["candidates"])}
    pending = sum(1 for m in movies if not m["dismissed"] and m["radarr_id"] not in done for c in m["candidates"] if c["state"] == "new")
    films_waiting = sum(1 for m in movies if not m["dismissed"] and m["radarr_id"] not in done and any(c["state"] == "new" for c in m["candidates"]))
    imported = sum(1 for m in movies for c in m["candidates"] if c["state"] == "imported")
    active = sum(1 for m in movies for c in m["candidates"] if c["state"] in ("queued", "downloading"))
    return {
        "to_review": pending,
        "films_waiting": films_waiting,
        "missing_tracked": len([m for m in movies if m["radarr_id"] != 0]) - len(done),
        "downloading": active,
        "imported": imported,
        "last_scan": store.kv_get("last_scan"),
        "scan": scanner.status,
        "vimeo": {"search": vimeo.enabled(), "cookies": os.path.exists(VIMEO_COOKIES)},
        "inbox": INBOX_SHARE,
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

    def _stream(self, path):
        """Serve a file with HTTP Range support so the preview player can seek."""
        size = os.path.getsize(path)
        ctype = MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:
                start = max(0, size - int(m.group(2)))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the player seeks by dropping connections; that is normal

    def do_GET(self):
        p = urlparse(self.path).path
        if p.startswith("/api/inbox-file/"):
            full = inbox.path_of(unquote(p[len("/api/inbox-file/"):]))
            if not full or not os.path.isfile(full):
                return self._json({"error": "not in inbox"}, 404)
            return self._stream(full)
        if p == "/api/missing":
            try:
                ms = radarr.missing_movies(0)
            except Exception as e:
                return self._json({"error": str(e)}, 502)
            return self._json(sorted(({"id": m["id"], "title": m["title"], "year": m.get("year")} for m in ms),
                                     key=lambda m: m["title"].lower()))
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
        m = re.fullmatch(r"/api/candidate/(\d+)/assign", p)
        if m:
            cid, mid = int(m.group(1)), int(body.get("movieId") or 0)
            c = store.candidate(cid)
            if not c or c["source"] != "inbox":
                return self._json({"error": "only inbox files can be assigned"}, 400)
            try:
                mv = radarr.movie(mid)
            except Exception:
                return self._json({"error": "no such Radarr movie"}, 404)
            if not store.movie(mid):
                store.upsert_movie({"radarr_id": mid, "title": mv["title"], "year": mv.get("year") or 0,
                                    "runtime": mv.get("runtime") or 0, "director": radarr.director(mid)})
            store.reassign(cid, mid, ["assigned by hand"])
            return self._json(store.candidate(cid))
        m = re.fullmatch(r"/api/candidate/(\d+)/(approve|reject|restore)", p)
        if m:
            cid, action = int(m.group(1)), m.group(2)
            c = store.candidate(cid)
            if not c:
                return self._json({"error": "no such candidate"}, 404)
            if action == "approve":
                if c["radarr_id"] == inbox.UNMATCHED:
                    return self._json({"error": "choose which film this is first"}, 409)
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
    inbox.ensure()
    downloader.start()
    threading.Thread(target=scanner.scheduler, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler)
    log.info("Deep Cut listening on :%d", WEB_PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
