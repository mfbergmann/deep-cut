"""
Living alongside Disc Two (https://github.com/mfbergmann/disc-two).

Disc Two imports films from DVD ISOs. Its review records say which film each
disc was confirmed as, and whether an import is running or done. A film that
is on disc must never get a web copy from Deep Cut: the disc is better, and
Disc Two may be writing into that film's folder right now.

So Deep Cut reads those records (mounted read-only at DISCTWO_REVIEW_DIR) and
treats any film Disc Two is importing or has imported as off-limits: not
searched, not approvable, labelled "on disc". Without the mount this is a
no-op.

The other direction lives in Disc Two: when it imports a disc's feature into
a film that already has a Deep Cut copy, it replaces that copy (after the disc
encode succeeds) instead of leaving two features in one folder.
"""
import json
import os
import time

DIR = os.environ.get("DISCTWO_REVIEW_DIR", "")
OWNED = ("importing", "imported")
_cache = {"at": 0, "by_tmdb": {}}


def enabled():
    return bool(DIR) and os.path.isdir(DIR)


def on_disc():
    """{tmdbId: {"iso": name, "status": status}} for films Disc Two owns."""
    if not enabled():
        return {}
    if time.time() - _cache["at"] < 60:
        return _cache["by_tmdb"]
    out = {}
    for name in os.listdir(DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(DIR, name)) as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        tmdb = (r.get("movie") or {}).get("tmdbId")
        if tmdb and r.get("status") in OWNED:
            out[int(tmdb)] = {"iso": name[:-5], "status": r["status"]}
    _cache.update(at=time.time(), by_tmdb=out)
    return out


def owner(tmdb_id):
    """The Disc Two record for this film, or None."""
    if not tmdb_id:
        return None
    return on_disc().get(int(tmdb_id))
