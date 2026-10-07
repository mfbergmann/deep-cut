"""
Vimeo, through its API. Where filmmakers and archives post their own work, so
it is often the cleanest copy of a short or a documentary that never had a
release.

Vimeo's search matches every word against the title, so a query is a bare
title (a year or director makes it find nothing). Each title variant is
searched on its own instead.
"""
import json
import urllib.parse
import urllib.request

from ..config import VIMEO_TOKEN

API = "https://api.vimeo.com"
FIELDS = "uri,name,duration,link,user.name,width,height,description,release_time,privacy.embed"


def enabled():
    return bool(VIMEO_TOKEN)


def _get(path, **params):
    req = urllib.request.Request(API + path + "?" + urllib.parse.urlencode(params))
    req.add_header("Authorization", f"bearer {VIMEO_TOKEN}")
    req.add_header("Accept", "application/vnd.vimeo.*+json;version=3.4")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def candidates(titles):
    if not enabled():
        return
    seen = set()
    for t in titles[:4]:
        try:
            data = _get("/videos", query=t, per_page=15, fields=FIELDS).get("data", [])
        except Exception:
            continue
        for v in data:
            vid = (v.get("uri") or "").rsplit("/", 1)[-1]
            if not vid or vid in seen:
                continue
            seen.add(vid)
            user = (v.get("user") or {}).get("name") or ""
            yield {
                "ext_id": vid,
                "title": v.get("name") or vid,
                "url": v.get("link") or f"https://vimeo.com/{vid}",
                "preview": f"https://player.vimeo.com/video/{vid}",
                "download": v.get("link") or f"https://vimeo.com/{vid}",
                "duration": v.get("duration"),
                "text": f"{user} {(v.get('release_time') or '')[:4]} {v.get('description') or ''}",
                "meta": {
                    "channel": user,
                    "resolution": f"{v.get('height')}p" if v.get("height") else None,
                    "embeddable": (v.get("privacy") or {}).get("embed") != "private",
                },
            }
