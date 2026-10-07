"""Finding candidates: which films to look for, and when."""
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime

from . import radarr, scoring, store
from .config import (MAX_CANDIDATES, MIN_AGE_DAYS, MIN_SCORE, RESCAN_DAYS,
                     SCAN_HOUR, YTDLP_BAKED, YTDLP_LOCAL)
from . import disctwo, inbox
from .config import INBOX_POLL
from .sources import archive, vimeo, youtube

log = logging.getLogger("deepcut.scan")

_scan_lock = threading.Lock()
status = {"running": False, "current": None, "done": 0, "total": 0, "last_error": None}


def ensure_ytdlp():
    """Keep a self-updating yt-dlp in /config; YouTube breaks old versions within weeks."""
    if not os.path.exists(YTDLP_LOCAL):
        os.makedirs(os.path.dirname(YTDLP_LOCAL), exist_ok=True)
        shutil.copy2(YTDLP_BAKED, YTDLP_LOCAL)
        os.chmod(YTDLP_LOCAL, 0o755)
    try:
        p = subprocess.run([YTDLP_LOCAL, "-U"], capture_output=True, text=True, timeout=180)
        log.info("yt-dlp update: %s", (p.stdout or p.stderr).strip().splitlines()[-1:] or "")
    except Exception as e:  # never let an update failure stop a scan
        log.warning("yt-dlp update failed: %s", e)


def _movie_info(m):
    return {
        "radarr_id": m["id"],
        "title": m["title"],
        "year": m.get("year") or 0,
        "runtime": m.get("runtime") or 0,
        "director": radarr.director(m["id"]),
    }


def search_movie(m):
    """Search every source for one Radarr movie; returns the number of new candidates."""
    info = _movie_info(m)
    store.upsert_movie(info)
    scoring_movie = {
        "titles": radarr.alt_titles(m),
        "year": info["year"],
        "runtime": info["runtime"],
        "director": info["director"],
    }
    found = []
    sources = [
        ("archive", lambda: archive.candidates(scoring_movie["titles"], info["year"], info["director"])),
        ("youtube", lambda: youtube.candidates(scoring_movie["titles"], info["year"], info["director"])),
        ("vimeo", lambda: vimeo.candidates(scoring_movie["titles"])),
    ]
    for name, fn in sources:
        detailed = 0
        try:
            for c in fn():
                pts, reasons, reject = scoring.score(scoring_movie, c["title"], c["duration"], c["text"])
                # Search results rarely carry the description, which is where a
                # re-score is usually admitted. Fetch it for plausible hits only.
                if name == "youtube" and not reject and pts >= MIN_SCORE and detailed < 10:
                    detailed += 1
                    d = youtube.details(c["ext_id"])
                    if d:
                        c["text"] = f"{c['text']} {d['description']}"
                        c["meta"]["resolution"] = f"{d['height']}p" if d.get("height") else None
                        pts, reasons, reject = scoring.score(scoring_movie, c["title"], c["duration"], c["text"])
                if reject:
                    continue
                c.update(source=name, radarr_id=info["radarr_id"], score=pts, reasons=reasons)
                if pts < MIN_SCORE:
                    # Not worth showing as new, but an existing row with this
                    # id must still get its lowered score.
                    store.add_candidate(c, insert=False)
                    continue
                found.append(c)
        except Exception as e:
            log.warning("%s search failed for %s: %s", name, info["title"], e)
    found.sort(key=lambda c: c["score"], reverse=True)
    # Every known candidate is re-scored (so a scoring change reaches it even
    # when it no longer makes the top N); only the top N are added as new.
    new = sum(1 for i, c in enumerate(found) if store.add_candidate(c, insert=i < MAX_CANDIDATES))
    store.mark_scanned(info["radarr_id"])
    return new


def scan(force=False, only_id=None):
    if not _scan_lock.acquire(blocking=False):
        return False
    try:
        status.update(running=True, done=0, total=0, current=None, last_error=None)
        ensure_ytdlp()
        all_missing = radarr.missing_movies(0)
        if only_id:
            movies = [m for m in all_missing if m["id"] == only_id]
        else:
            # Forget films that are no longer missing at all -- not merely
            # those too new to search, which may already have inbox files.
            store.drop_movies_not_in([m["id"] for m in all_missing])
            eligible = {m["id"] for m in radarr.missing_movies(MIN_AGE_DAYS)}
            movies = [m for m in all_missing if m["id"] in eligible]
        due = []
        for m in movies:
            disc = disctwo.owner(m.get("tmdbId"))
            if disc:
                if not store.movie(m["id"]):
                    store.upsert_movie(_movie_info(m))
                store.set_on_disc(m["id"], disc["iso"])
                continue
            if store.movie(m["id"]):
                store.set_on_disc(m["id"], None)
            known = store.movie(m["id"])
            if known and known.get("dismissed") and not only_id:
                continue
            if not force and known and known.get("last_scan") and \
                    time.time() - known["last_scan"] < RESCAN_DAYS * 86400:
                continue
            due.append(m)
        status["total"] = len(due)
        new_total = 0
        for m in due:
            status["current"] = m["title"]
            new_total += search_movie(m)
            status["done"] += 1
            time.sleep(2)  # be polite to both sources
        store.kv_set("last_scan", {"at": time.time(), "searched": len(due), "new": new_total})
        log.info("scan complete: %d films searched, %d new candidates", len(due), new_total)
        return True
    except Exception as e:
        status["last_error"] = str(e)
        log.exception("scan failed")
        return False
    finally:
        status.update(running=False, current=None)
        _scan_lock.release()


def start_scan(force=False, only_id=None):
    t = threading.Thread(target=scan, kwargs={"force": force, "only_id": only_id}, daemon=True)
    t.start()


def _poll_inbox():
    try:
        inbox.poll()
    except Exception:
        log.exception("inbox poll failed")


def scheduler():
    """Inbox every INBOX_POLL seconds; full search daily at SCAN_HOUR (and at
    startup if the last one is stale)."""
    _poll_inbox()
    last = store.kv_get("last_scan") or {}
    if time.time() - last.get("at", 0) > 86400:
        time.sleep(30)
        scan()
    while True:
        _poll_inbox()
        if datetime.now().hour == SCAN_HOUR:
            last = store.kv_get("last_scan") or {}
            if time.time() - last.get("at", 0) > 20 * 3600:
                scan()
        time.sleep(INBOX_POLL)
