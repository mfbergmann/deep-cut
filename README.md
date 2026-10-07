# Deep Cut

A last-resort finder for films Radarr cannot get. Early shorts, experimental
work, TV films and out-of-print documentaries often never appear on an indexer
but do survive on the **Internet Archive** or **YouTube**. Deep Cut looks there,
scores what it finds against what Radarr knows about the film, and puts the
candidates in front of a person. **Nothing is downloaded until someone approves
it.**

Web UI: `http://<host>:8473` · Homepage tile: Media Management → Deep Cut

## How it works

1. **Which films.** Daily at `SCAN_HOUR` (03:00), every Radarr movie that is
   monitored, released (`isAvailable`), has no file, is not in Radarr's queue,
   and was added more than `MIN_AGE_DAYS` (14) ago. A film is searched again
   after `RESCAN_DAYS` (7). Films that get a file drop off automatically.
2. **Where.**
   - **Internet Archive**: search API plus per-item metadata, which gives exact
     file durations and the uploader's *original* file. Queried by title, then
     narrowed by year and director, plus the part of the title before a colon.
   - **YouTube**: `yt-dlp` flat search, then full metadata for the plausible
     hits, because a re-score is usually admitted only in the description.
   - **Vimeo**: the API (`VIMEO_TOKEN`, public scope). Its search ANDs every
     word against the title, so each title variant is queried bare. Downloads
     need a logged-in session: `appdata/deep-cut/vimeo-cookies.txt` (a
     cookies.txt containing **only** vimeo.com cookies), used through
     `yt-dlp --impersonate chrome`. Vimeo blocks every anonymous server-side
     route, including its oEmbed and player endpoints.
   - **The inbox**: `\\wintermute\data\deepcut\inbox`. Drop in a file you got
     yourself (Downie, a rip). Once its size holds still it is matched to a
     missing film, by `tmdb-123` / `radarr-123` in the name if present,
     otherwise by title and runtime. Unmatched files get a film picker. Approve
     moves it out of the inbox and imports it. Previews stream in the page.
3. **Scoring (0–100).** Runtime against TMDB's is the strongest signal (±5% =
   40 points; under half the length is rejected outright as a trailer or clip).
   Title words must match (under 50% coverage is rejected; under 75% caps the
   score at 40). Year +10, director's surname +15. "Trailer", "review",
   "clip", "Q&A", etc. in the upload's title cost 40. **Altered versions**
   (new soundtrack, colorized, AI upscale, fan edit) cost 25 and are labelled
   `ALTERED` in the UI. They are still the film, just not as made. Only
   candidates scoring ≥ `MIN_SCORE` (45) are kept, at most 6 new per film.
4. **Review.** Preview inline, then **Approve**, **Reject** (stays rejected
   across rescans), or **Dismiss film** (never searched again).
5. **Import.** An approved candidate downloads into `/data/deepcut/<radarrId>/`
   (Internet Archive: direct HTTP of the chosen file; YouTube: `yt-dlp`, best
   video+audio merged to MKV), then a Radarr **ManualImport** (move) files it
   as that film. Quality is usually Unknown or WEB, so Radarr will still upgrade
   it if a proper release appears later (the `03 Any` profile allows Unknown and
   has upgrades on).

## Running it

Its own compose project, deliberately not a service in `arrs`. It joins
`arrs-network` as an **external** network (so `http://radarr:7878` resolves),
and must therefore start after `arrs`; the order is pinned in
`/boot/config/plugins/compose.manager/stack-order.json`. It mounts
`/mnt/user/data` at `/data` exactly as Radarr does, so staging paths are valid
for Radarr verbatim. Runs as 99:100.

```bash
cd /mnt/user/docker/projects/deep-cut
docker compose build && docker compose up -d
docker logs -f deep-cut
```

`.env` (not committed): `RADARR_API_KEY`, plus optional `MIN_AGE_DAYS`,
`RESCAN_DAYS`, `SCAN_HOUR`, `MIN_SCORE`.

State lives in `/mnt/user/appdata/deep-cut/` (`deepcut.db`, and `bin/yt-dlp`,
a self-updating copy that runs `yt-dlp -U` before every scan, because YouTube
breaks old versions within weeks). The image also carries **deno**, the
JavaScript runtime yt-dlp needs for YouTube's player challenges.

## API

| Method | Path | |
|---|---|---|
| GET | `/api/summary` | counts for the Homepage widget |
| GET | `/api/films` | everything, grouped by film |
| POST | `/api/scan` | `{}` = all films now; `{"movieId": N}` = one film |
| POST | `/api/candidate/<id>/approve\|reject\|restore` | |
| POST | `/api/film/<radarrId>/dismiss\|undismiss` | |
| POST | `/api/candidate/<id>/assign` | `{"movieId": N}`, for an unmatched inbox file |
| GET | `/api/missing` | missing films, for the picker |
| GET | `/api/inbox-file/<path>` | streams an inbox file (Range-aware) for preview |

## Not (yet) included

UbuWeb has no search or API and its catalogue changes (titles are removed on
request), so it was left out of v1. A crawler that builds a local
artist/title/year index from its film pages would slot in as a third source in
`deepcut/sources/`.
