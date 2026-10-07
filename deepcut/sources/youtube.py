"""
YouTube via yt-dlp. The biggest source and the least trustworthy: most hits
for a film's title are trailers, clips, reviews and re-uploads. Search only
lists; nothing is fetched until a person approves a candidate.
"""
import json
import os
import subprocess

from ..config import YTDLP_BAKED, YTDLP_LOCAL


def ytdlp():
    return YTDLP_LOCAL if os.path.exists(YTDLP_LOCAL) else YTDLP_BAKED


def _search(query, n=10):
    try:
        p = subprocess.run(
            [ytdlp(), "--flat-playlist", "-j", "--no-warnings", "--no-update", f"ytsearch{n}:{query}"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return []
    out = []
    for line in p.stdout.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def candidates(titles, year, director):
    main = titles[0]
    surname = (director or "").split(",")[0].strip()
    queries = [f"{main} {year} {surname}".strip(), f"{main} {year} full film"]
    # A shorter form of the title (the part before a colon) is often how it
    # was uploaded.
    for t in titles[1:]:
        if len(t) < len(main) and main.lower().startswith(t.lower()):
            queries.append(f"{t} {year} {surname}".strip())
            break
    seen = set()
    for q in queries:
        for e in _search(q):
            vid = e.get("id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            yield {
                "ext_id": vid,
                "title": e.get("title") or vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "preview": f"https://www.youtube-nocookie.com/embed/{vid}",
                "download": f"https://www.youtube.com/watch?v={vid}",
                "duration": e.get("duration"),
                "text": f"{e.get('channel') or e.get('uploader') or ''} {e.get('description') or ''}",
                "meta": {
                    "channel": e.get("channel") or e.get("uploader"),
                    "views": e.get("view_count"),
                },
            }
