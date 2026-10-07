"""
Internet Archive. A real search API, stable item pages, and -- what matters most
here -- per-file durations and the uploader's original file, which is often a
full-quality DVD or WEB rip rather than the archive's own h.264 derivative.
"""
import json
import re
import urllib.parse
import urllib.request

UA = "DeepCut/1.0 (personal media library; +https://archive.org/about/)"
VIDEO_EXT = re.compile(r"\.(mkv|mp4|m4v|avi|mov|mpe?g|ogv|webm|wmv)$", re.I)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _q(s):
    # Lucene phrase; strip characters that break the query.
    return '"' + re.sub(r'["\\:()\[\]{}^~*?]', " ", s).strip() + '"'


def queries(titles, year, director):
    """Plain title first, then narrowed by year and director.

    A common title ("Macbeth") returns hundreds of items and the right one is
    rarely in the first page; adding the year or director is what finds it.
    """
    surname = ""
    if director:
        first = director.split(",")[0].strip().split()
        surname = first[-1] if first else ""
    qs = []
    for t in titles[:3]:
        base = f"title:({_q(t)}) AND mediatype:(movies)"
        qs.append(base)
        if year:
            qs.append(f"{base} AND (year:{year} OR date:{year}* OR title:({year}))")
        if surname:
            qs.append(f"{base} AND (creator:({_q(surname)}) OR description:({_q(surname)}) OR title:({_q(surname)}))")
    seen, out = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def search(titles, year, director="", limit=12):
    ids, out = set(), []
    for q in queries(titles, year, director):
        url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(
            [("q", q), ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "year"),
             ("fl[]", "creator"), ("fl[]", "downloads"), ("rows", str(limit)), ("output", "json")]
        )
        try:
            docs = _get(url)["response"]["docs"]
        except Exception:
            continue
        for d in docs:
            if d["identifier"] not in ids:
                ids.add(d["identifier"])
                out.append(d)
    return out


def parse_length(v):
    if v is None:
        return None
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    parts = s.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def best_file(identifier):
    """The file worth downloading: the largest original video, else the largest derivative."""
    meta = _get(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    files = [f for f in meta.get("files", []) if VIDEO_EXT.search(f.get("name", ""))]
    if not files:
        return meta, None
    def key(f):
        return (f.get("source") == "original", int(f.get("size") or 0))
    f = max(files, key=key)
    return meta, f


def candidates(titles, year, director=""):
    """Yield dicts: ext_id, title, url, preview, download, duration, text, meta."""
    for doc in search(titles, year, director)[:25]:
        ident = doc["identifier"]
        try:
            meta, f = best_file(ident)
        except Exception:
            continue
        if not f:
            continue
        md = meta.get("metadata", {})
        title = doc.get("title") or md.get("title") or ident
        if isinstance(title, list):
            title = title[0]
        creator = md.get("creator") or doc.get("creator") or ""
        if isinstance(creator, list):
            creator = ", ".join(creator)
        desc = md.get("description") or ""
        if isinstance(desc, list):
            desc = " ".join(desc)
        yield {
            "ext_id": ident,
            "title": str(title),
            "url": f"https://archive.org/details/{ident}",
            "preview": f"https://archive.org/embed/{ident}",
            "download": f"https://archive.org/download/{ident}/{urllib.parse.quote(f['name'])}",
            "duration": parse_length(f.get("length")),
            "text": f"{creator} {md.get('date', '')} {md.get('year', '')} {re.sub('<[^>]+>', ' ', desc)[:600]}",
            "meta": {
                "file": f["name"],
                "size": int(f.get("size") or 0),
                "resolution": f"{f.get('width', '?')}x{f.get('height', '?')}",
                "original": f.get("source") == "original",
                "creator": creator,
                "downloads": doc.get("downloads"),
            },
        }
