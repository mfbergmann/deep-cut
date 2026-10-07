"""
Is this upload the film?

Duration is the strongest single signal: a trailer, a clip, a review and a
supercut all have the wrong length. It is not sufficient on its own -- an
interview with the subject of a documentary can run exactly as long as the
documentary -- so the title has to match too, and a weak title match caps the
score below the review threshold no matter how good the rest looks.

Scores are a sorting aid for a person, not a decision.
"""
import re
import unicodedata

STOP = {"the", "a", "an", "of", "and", "in", "on", "to", "la", "le", "les", "el", "der", "die", "das"}

# Words that mark something *about* the film rather than the film. Ignored
# when the film's own title contains them.
NOT_THE_FILM = [
    "trailer", "teaser", "clip", "clips", "review", "reaction", "explained",
    "behind the scenes", "making of", "interview", "scene", "supercut",
    "podcast", "essay", "analysis", "breakdown", "recap", "tribute",
    "soundtrack", "ost", "q&a", "premiere", "excerpt", "promo", "featurette",
    "commentary", "remix", "cover", "music video",
]


# The film, but not as made: a new score over a silent film, a colorization,
# an upscale, a fan edit. Still the right film, so these are shown -- ranked
# below the original and labelled, so nobody imports one by accident.
ALTERED = [
    ("music added", "new soundtrack"), ("new soundtrack", "new soundtrack"),
    ("new soudntrack", "new soundtrack"), ("new music", "new soundtrack"),
    ("new score", "new soundtrack"), ("rescore", "new soundtrack"), ("re score", "new soundtrack"),
    ("sound design", "new soundtrack"), ("original music by", "new soundtrack"),
    ("music by", "new soundtrack"), ("musica", "new soundtrack"), ("new audio", "new soundtrack"),
    ("colorized", "colorized"), ("colourized", "colorized"), ("a color", "colorized"),
    ("in color", "colorized"), ("upscale", "AI upscale"), ("upscaled", "AI upscale"),
    ("ai enhanced", "AI upscale"), ("4k remaster ai", "AI upscale"), ("fan edit", "fan edit"),
    ("re edit", "fan edit"), ("reedit", "fan edit"),
]


# Phrases that, in an upload's *description*, mean the soundtrack is not the
# original. Narrower than ALTERED, because descriptions mention music freely
# ("score by Teiji Ito" can describe the original).
ALTERED_DESC = [
    ("improvisation to", "live re-score"), ("improvised", "live re-score"),
    ("live score", "live re-score"), ("cine concert", "live re-score"),
    ("cine-concert", "live re-score"), ("musique originale", "new soundtrack"),
    ("new soundtrack", "new soundtrack"), ("new score", "new soundtrack"),
    ("rescored", "new soundtrack"), ("re scored", "new soundtrack"),
    ("my own soundtrack", "new soundtrack"), ("added music", "new soundtrack"),
    ("music added", "new soundtrack"), ("colorized", "colorized"),
    ("ai upscal", "AI upscale"), ("upscaled with", "AI upscale"),
]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = s.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def tokens(s):
    return [t for t in norm(s).split() if t not in STOP]


def title_coverage(movie_titles, cand_title):
    """Best fraction of any known title's words that appear in the candidate."""
    ct = set(tokens(cand_title))
    best = 0.0
    for t in movie_titles:
        tt = tokens(t)
        if not tt:
            continue
        cov = sum(1 for w in tt if w in ct) / len(tt)
        best = max(best, cov)
    return best


def score(movie, cand_title, duration, extra_text=""):
    """Return (score, reasons, reject).

    movie: dict with titles (list), year, runtime (minutes), director.
    """
    reasons = []
    pts = 0
    text = norm(cand_title + " " + (extra_text or ""))
    title_n = norm(cand_title)

    # Title
    cov = title_coverage(movie["titles"], cand_title)
    if cov < 0.5:
        return 0, ["title does not match"], True
    if cov >= 1.0:
        pts += 30
        reasons.append("title matches")
    elif cov >= 0.75:
        pts += 18
        reasons.append("title mostly matches")
    else:
        reasons.append(f"title only partly matches ({int(cov * 100)}%)")

    # Duration
    runtime_s = (movie.get("runtime") or 0) * 60
    if duration and runtime_s:
        ratio = duration / runtime_s
        off = abs(1 - ratio)
        if ratio < 0.5:
            return 0, [f"far too short ({_mmss(duration)} vs {_mmss(runtime_s)})"], True
        if off <= 0.05:
            pts += 40
            reasons.append(f"runtime matches ({_mmss(duration)})")
        elif off <= 0.10:
            pts += 30
            reasons.append(f"runtime close ({_mmss(duration)} vs {_mmss(runtime_s)})")
        elif off <= 0.20:
            pts += 10
            reasons.append(f"runtime differs ({_mmss(duration)} vs {_mmss(runtime_s)})")
        else:
            pts -= 20
            reasons.append(f"runtime wrong ({_mmss(duration)} vs {_mmss(runtime_s)})")
    elif duration and duration < 60:
        return 0, ["under a minute"], True
    else:
        reasons.append("runtime unknown")

    # Year and director
    year = movie.get("year")
    if year and re.search(rf"\b{year}\b", text):
        pts += 10
        reasons.append(f"year {year}")
    surnames = [norm(d).split()[-1] for d in (movie.get("director") or "").split(",") if norm(d)]
    if any(s and re.search(rf"\b{re.escape(s)}\b", text) for s in surnames):
        pts += 15
        reasons.append("director named")

    # Not-the-film markers (title only: descriptions mention trailers freely)
    movie_title_n = " ".join(norm(t) for t in movie["titles"])
    hits = [w for w in NOT_THE_FILM if re.search(rf"\b{re.escape(w)}\b", title_n) and w not in movie_title_n]
    if hits:
        pts -= 40
        reasons.append("looks like a " + hits[0])

    altered = {label for w, label in ALTERED if re.search(rf"\b{re.escape(w)}\b", title_n) and w not in movie_title_n}
    desc_n = norm(extra_text or "")
    altered |= {label for w, label in ALTERED_DESC if norm(w) in desc_n and norm(w) not in movie_title_n}
    altered = sorted(altered)
    if altered:
        pts -= 25
        reasons.append("ALTERED: " + ", ".join(altered))

    if re.search(r"\bfull (movie|film)\b|\bcomplete film\b", title_n):
        pts += 5
        reasons.append("labelled full film")

    # A one-word title ("Nadja", "Swimmer") matches far too much on its own.
    if len(tokens(movie["titles"][0])) <= 1 and "director named" not in reasons and f"year {year}" not in reasons:
        pts = min(pts, 40)
        reasons.append("short title with no year or director to confirm it")

    if cov < 0.75:
        pts = min(pts, 40)

    return max(0, min(100, pts)), reasons, False


def _mmss(sec):
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
