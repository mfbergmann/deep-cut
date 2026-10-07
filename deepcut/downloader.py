"""
Approved candidates are fetched one at a time and handed to Radarr.

Each film gets its own staging folder, so a manual import can only ever see
the file that was approved for it.
"""
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import urllib.request

from . import radarr, store
from . import inbox
from .config import STAGING_DIR, VIMEO_COOKIES
from .sources.archive import UA
from .sources.youtube import ytdlp

log = logging.getLogger("deepcut.download")
_q = queue.Queue()
# A finished video: yt-dlp intermediates (.fNNN.ext) and .part files do not count.
VIDEO = re.compile(r"(?<!\.f\d{3})\.(mkv|mp4|m4v|avi|mov|mpe?g|ogv|webm|wmv)$", re.I)


def enqueue(cid):
    store.set_state(cid, "queued")
    _q.put(cid)


def resume_pending():
    """Requeue anything that was queued or mid-download when the container stopped."""
    for m in store.overview():
        for c in m["candidates"]:
            if c["state"] in ("queued", "downloading"):
                _q.put(c["id"])


def _fetch_archive(url, dest_dir, name):
    path = os.path.join(dest_dir, os.path.basename(name))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(path + ".part", "wb") as f:
        shutil.copyfileobj(r, f, length=4 * 1024 * 1024)
    os.replace(path + ".part", path)


def _fetch_youtube(url, dest_dir):
    p = subprocess.run(
        [ytdlp(), "--no-update", "--no-playlist", "-f", "bv*+ba/b",
         "--merge-output-format", "mkv", "--no-part", "--restrict-filenames",
         "-o", os.path.join(dest_dir, "%(title).120B [%(id)s].%(ext)s"), url],
        capture_output=True, text=True, timeout=6 * 3600,
    )
    if p.returncode != 0:
        tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
        raise RuntimeError("yt-dlp: " + " | ".join(tail))


VIMEO_HELP = ("Vimeo needs a logged-in session: export cookies.txt from a browser logged in to "
              "vimeo.com and save it as appdata/deep-cut/vimeo-cookies.txt -- or download the video "
              "yourself (e.g. with Downie) and drop it in the inbox.")


def _fetch_vimeo(url, dest_dir):
    if not os.path.exists(VIMEO_COOKIES):
        raise RuntimeError(VIMEO_HELP)
    p = subprocess.run(
        [ytdlp(), "--no-update", "--no-playlist", "--impersonate", "chrome",
         "--cookies", VIMEO_COOKIES, "-f", "bv*+ba/b", "--merge-output-format", "mkv",
         "--no-part", "--restrict-filenames",
         "-o", os.path.join(dest_dir, "%(title).120B [vimeo-%(id)s].%(ext)s"), url],
        capture_output=True, text=True, timeout=6 * 3600,
    )
    if p.returncode != 0:
        err = (p.stderr or p.stdout).strip()
        if "logged-in" in err or "cookies" in err.lower():
            raise RuntimeError("Vimeo login expired or refused. " + VIMEO_HELP)
        raise RuntimeError("yt-dlp: " + " | ".join(err.splitlines()[-3:]))


def _take_from_inbox(rel, dest_dir):
    src = inbox.path_of(rel)
    if not src or not os.path.isfile(src):
        raise RuntimeError("the file is no longer in the inbox")
    shutil.move(src, os.path.join(dest_dir, os.path.basename(src)))
    parent = os.path.dirname(src)
    if os.path.realpath(parent) != os.path.realpath(inbox.INBOX_DIR):
        try:
            os.rmdir(parent)  # remove a now-empty wrapper folder
        except OSError:
            pass


def process(cid):
    c = store.candidate(cid)
    if not c or c["state"] not in ("queued", "downloading"):
        return
    if c["radarr_id"] == inbox.UNMATCHED:
        store.set_state(cid, "new", "choose which film this is before approving")
        return
    m = radarr.movie(c["radarr_id"])
    if m.get("hasFile"):
        store.set_state(cid, "failed", "film already has a file in Radarr; nothing downloaded")
        return
    dest = os.path.join(STAGING_DIR, str(c["radarr_id"]))
    marker = os.path.join(dest, ".candidate")
    # A finished download for this same candidate is reused (a retry after a
    # failed import should not fetch gigabytes again). Anything else in the
    # folder -- another candidate's file, or a partial download -- is cleared.
    reuse = False
    if os.path.isdir(dest):
        try:
            same = open(marker).read().strip() == str(cid)
        except OSError:
            same = False
        done = [f for f in os.listdir(dest) if VIDEO.search(f)]
        if same and done:
            reuse = True
        else:
            shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    with open(marker, "w") as f:
        f.write(str(cid))
    store.set_state(cid, "downloading")
    try:
        if reuse:
            log.info("reusing finished download for candidate %s", cid)
        elif c["source"] == "archive":
            _fetch_archive(c["download"], dest, c["meta"].get("file") or "video")
        elif c["source"] == "vimeo":
            _fetch_vimeo(c["download"], dest)
        elif c["source"] == "inbox":
            _take_from_inbox(c["download"], dest)
        else:
            _fetch_youtube(c["download"], dest)
        ok, msg = radarr.manual_import(c["radarr_id"], dest)
        if ok:
            store.set_state(cid, "imported")
            shutil.rmtree(dest, ignore_errors=True)
            log.info("imported %s for radarr %s", c["title"], c["radarr_id"])
        else:
            store.set_state(cid, "failed", msg)
    except Exception as e:
        log.exception("download failed")
        store.set_state(cid, "failed", str(e)[:500])


def worker():
    while True:
        cid = _q.get()
        try:
            process(cid)
        except Exception:
            log.exception("worker error")
        finally:
            _q.task_done()


def start():
    threading.Thread(target=worker, daemon=True).start()
    resume_pending()
