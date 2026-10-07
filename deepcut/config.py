import os


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


RADARR_URL = os.environ.get("RADARR_URL", "http://radarr:7878").rstrip("/")
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")

STATE_DIR = os.environ.get("STATE_DIR", "/config")
DB_PATH = os.path.join(STATE_DIR, "deepcut.db")

# Must be visible to Radarr at this exact path, or the manual import cannot
# find the file. Both containers mount the same share at /data.
STAGING_DIR = os.environ.get("STAGING_DIR", "/data/deepcut")

WEB_PORT = _int("WEB_PORT", 8473)

# A film is only worth a fallback search once the normal pipeline has had a
# fair chance at it.
MIN_AGE_DAYS = _int("MIN_AGE_DAYS", 14)
# How long before an already-searched film is searched again.
RESCAN_DAYS = _int("RESCAN_DAYS", 7)
# Local hour of the daily scan.
SCAN_HOUR = _int("SCAN_HOUR", 3)

MIN_SCORE = _int("MIN_SCORE", 45)
MAX_CANDIDATES = _int("MAX_CANDIDATES", 6)

# A self-updating copy lives in /config so YouTube fixes do not wait for an
# image rebuild. The baked-in copy is the fallback.
YTDLP_LOCAL = os.path.join(STATE_DIR, "bin", "yt-dlp")
YTDLP_BAKED = "/usr/local/bin/yt-dlp"
