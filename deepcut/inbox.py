"""
The inbox: files a person fetched themselves.

Some copies can only be had by hand -- a Vimeo video that only a logged-in
browser can download, a disc someone ripped, a file from a friend. Drop it in
the inbox; Deep Cut waits until it stops growing, reads its runtime, matches it
to a missing film and lists it for review like any other candidate. Approving
moves it out of the inbox and imports it.

Matching, best first: an explicit `tmdb-123` or `radarr-123` in the file name;
otherwise the same title/runtime scoring every other source gets. Files that
match nothing are listed under "Unmatched inbox files" with a film picker.
"""
import json
import logging
import os
import re
import subprocess
import time
import urllib.parse

from . import radarr, scoring, store
from .config import INBOX_DIR

log = logging.getLogger("deepcut.inbox")
VIDEO = re.compile(r"\.(mkv|mp4|m4v|avi|mov|mpe?g|ogv|webm|wmv|ts|m2ts)$", re.I)
UNMATCHED = 0
_missing_cache = {"at": 0, "movies": []}


def ensure():
    os.makedirs(INBOX_DIR, exist_ok=True)
    try:
        os.chmod(INBOX_DIR, 0o777)  # written to over SMB by a different user
    except OSError:
        pass


def _files():
    """Video files at the top level, or one folder down (some tools wrap files in a folder)."""
    out = []
    for name in sorted(os.listdir(INBOX_DIR)):
        p = os.path.join(INBOX_DIR, name)
        if os.path.isfile(p) and VIDEO.search(name):
            out.append(name)
        elif os.path.isdir(p):
            for sub in sorted(os.listdir(p)):
                if VIDEO.search(sub) and os.path.isfile(os.path.join(p, sub)):
                    out.append(f"{name}/{sub}")
    return out


def path_of(rel):
    """Resolve an inbox-relative path, refusing anything that escapes the inbox."""
    full = os.path.realpath(os.path.join(INBOX_DIR, rel))
    root = os.path.realpath(INBOX_DIR)
    if full != root and full.startswith(root + os.sep):
        return full
    return None


def duration(path):
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        )
        return float(json.loads(p.stdout)["format"]["duration"])
    except Exception:
        return None


def _missing():
    if time.time() - _missing_cache["at"] > 600:
        _missing_cache["movies"] = radarr.missing_movies(0)
        _missing_cache["at"] = time.time()
    return _missing_cache["movies"]


def _clean_title(rel):
    stem = os.path.splitext(os.path.basename(rel))[0]
    return re.sub(r"[._]+", " ", stem)


def match(rel, dur):
    """Return (movie or None, score, reasons)."""
    name = rel.lower()
    movies = _missing()
    m = re.search(r"tmdb[-_ ]?(\d+)", name)
    if m:
        for mv in movies:
            if str(mv.get("tmdbId")) == m.group(1):
                return mv, 100, ["matched by TMDB id in file name"]
    m = re.search(r"radarr[-_ ]?(\d+)", name)
    if m:
        for mv in movies:
            if str(mv["id"]) == m.group(1):
                return mv, 100, ["matched by Radarr id in file name"]
    title = _clean_title(rel)
    best = (None, 0, ["no missing film matches this name"])
    for mv in movies:
        sm = {"titles": radarr.alt_titles(mv), "year": mv.get("year"),
              "runtime": mv.get("runtime") or 0, "director": ""}
        pts, reasons, reject = scoring.score(sm, title, dur, "")
        if not reject and pts > best[1]:
            best = (mv, pts, reasons)
    if best[1] >= 30:
        return best
    return None, 0, best[2] if best[0] is None else ["best guess too weak: " + best[0]["title"]]


def _ensure_unmatched_row():
    if not store.movie(UNMATCHED):
        store.upsert_movie({"radarr_id": UNMATCHED, "title": "Unmatched inbox files",
                            "year": 0, "runtime": 0, "director": ""})


def poll():
    ensure()
    files = _files()
    sizes = store.kv_get("inbox_sizes", {})
    new_sizes = {}
    known = {c["ext_id"]: c for m in store.overview() for c in m["candidates"] if c["source"] == "inbox"}

    # Forget unreviewed entries whose file was removed from the inbox.
    for rel, c in known.items():
        if rel not in files and c["state"] == "new":
            store.delete_candidate(c["id"])

    for rel in files:
        full = os.path.join(INBOX_DIR, rel)
        try:
            st = os.stat(full)
        except OSError:
            continue
        new_sizes[rel] = st.st_size
        if rel in known:
            continue
        # Still being copied in: wait for the size to hold still across a poll
        # and the file to be a minute old.
        if sizes.get(rel) != st.st_size or time.time() - st.st_mtime < 60:
            continue
        dur = duration(full)
        mv, pts, reasons = match(rel, dur)
        if mv:
            rid = mv["id"]
            if not store.movie(rid):
                store.upsert_movie({"radarr_id": rid, "title": mv["title"], "year": mv.get("year") or 0,
                                    "runtime": mv.get("runtime") or 0, "director": radarr.director(rid)})
        else:
            rid = UNMATCHED
            _ensure_unmatched_row()
        url = "/api/inbox-file/" + urllib.parse.quote(rel)
        store.add_candidate({
            "radarr_id": rid, "source": "inbox", "ext_id": rel, "title": os.path.basename(rel),
            "url": url, "preview": url, "download": rel, "duration": dur,
            "score": pts, "reasons": reasons,
            "meta": {"size": st.st_size, "file": rel},
        })
        log.info("inbox: %s -> %s (%s)", rel, mv["title"] if mv else "unmatched", pts)
    store.kv_set("inbox_sizes", new_sizes)
