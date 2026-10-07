"""The small slice of the Radarr v3 API Deep Cut uses."""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .config import RADARR_API_KEY, RADARR_URL


def _req(method, path, params=None, body=None, timeout=60):
    url = RADARR_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Api-Key", RADARR_API_KEY)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def get(path, **params):
    return _req("GET", path, params=params)


def post(path, body):
    return _req("POST", path, body=body)


def _age_days(iso):
    if not iso:
        return 0
    try:
        added = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return (datetime.now(timezone.utc) - added).total_seconds() / 86400


def missing_movies(min_age_days):
    """Monitored, released, fileless films that are not already downloading."""
    queued = set()
    page = get("/api/v3/queue", pageSize=1000) or {}
    for rec in page.get("records", []):
        if rec.get("movieId"):
            queued.add(rec["movieId"])
    out = []
    for m in get("/api/v3/movie") or []:
        if not m.get("monitored") or m.get("hasFile") or not m.get("isAvailable"):
            continue
        if m["id"] in queued:
            continue
        if _age_days(m.get("added")) < min_age_days:
            continue
        out.append(m)
    return out


def director(movie_id):
    try:
        credits = get("/api/v3/credit", movieId=movie_id) or []
    except Exception:
        return ""
    names = [c.get("personName", "") for c in credits if c.get("job") == "Director"]
    return ", ".join(n for n in names if n)


def alt_titles(m):
    titles = [m.get("title") or ""]
    if m.get("originalTitle"):
        titles.append(m["originalTitle"])
    for a in m.get("alternateTitles") or []:
        t = a.get("title")
        if t:
            titles.append(t)
    seen, out = set(), []
    for t in titles:
        k = t.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(t.strip())
    return out


def movie(movie_id):
    return get(f"/api/v3/movie/{movie_id}")


def manual_import(movie_id, folder):
    """Import every video file in `folder` as `movie_id`, moving it into the library.

    Returns (ok, message). Radarr's own matching is ignored on purpose: these
    files are named however the uploader named them, and a person has already
    confirmed which film this is.
    """
    items = get("/api/v3/manualimport", folder=folder, movieId=movie_id, filterExistingFiles="false") or []
    files = []
    for it in items:
        path = it.get("path", "")
        if not path:
            continue
        files.append({
            "path": path,
            "movieId": movie_id,
            "quality": it.get("quality") or {"quality": {"id": 0, "name": "Unknown"}, "revision": {"version": 1}},
            "languages": it.get("languages") or [{"id": 1, "name": "English"}],
            "releaseGroup": it.get("releaseGroup") or "DeepCut",
            "downloadId": "",
        })
    if not files:
        return False, f"Radarr found no importable video in {folder}"
    # Only the largest file is the film; anything else (thumbnails, samples)
    # must not be imported over it.
    files = files[:1] if len(files) == 1 else [max(files, key=lambda f: _size(items, f["path"]))]
    cmd = post("/api/v3/command", {"name": "ManualImport", "importMode": "move", "files": files})
    cid = cmd.get("id")
    for _ in range(120):
        time.sleep(5)
        st = get(f"/api/v3/command/{cid}")
        if st.get("status") in ("completed", "failed"):
            break
    m = movie(movie_id)
    if m.get("hasFile"):
        return True, "imported"
    return False, f"Radarr import did not produce a file (command {st.get('status')}: {st.get('message', '')})"


def _size(items, path):
    for it in items:
        if it.get("path") == path:
            return it.get("size") or 0
    return 0
