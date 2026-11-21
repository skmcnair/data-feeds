# data-feeds

Public data feeds for wallboards and other uses, kept current every day.
Claude does the judgment (what's relevant, the facts and where they came
from); code on GitHub does everything else, the same way every time.

## How a feed works

1. **Collect** (GitHub, every 3 hours): `engine/collect.py` pulls each feed's
   sources (RSS, RSS.app, Google News searches) into its waiting list,
   `feeds/<feed>/inbox.json`.
2. **Review** (Claude, daily scheduled task): works through the waiting list,
   searches for anything missed, re-checks open items, finds logos, and
   commits one small change file, `feeds/<feed>/changes/<time>.json`.
   Instructions: [docs/REVIEW.md](docs/REVIEW.md).
3. **Publish** (GitHub, on every change and hourly): `engine/apply.py` checks
   and applies change files to the master records, `feeds/<feed>/records.json`.
   `engine/build.py` lays Sean's corrections on top, formats everything, checks
   it, and publishes `feed.json` and `feed.csv`. If anything fails its checks,
   the last good feed stays up.
4. **Health** (GitHub, every morning): `engine/validate.py --health` confirms
   every feed was collected, reviewed and published. If not, it opens a
   `feed-health` issue, which emails the repo owner, and closes it when healthy.
5. **Logos** (GitHub, Mondays): `engine/logos.py check` re-reads every logo
   from its source. A new logo has to show on two checks a week apart, then
   Claude confirms it before it's used.

## Addresses

| What | Where |
| --- | --- |
| A feed | `https://raw.githubusercontent.com/skmcnair/data-feeds/main/feeds/<feed>/feed.json` |
| Same rows for Excel | `.../feeds/<feed>/feed.csv` |
| Tracker page | `https://skmcnair.github.io/data-feeds/tracker/?feed=<feed>` |
| All feeds | `https://skmcnair.github.io/data-feeds/tracker/` and `feeds/index.json` |
| Health of a feed | `.../feeds/<feed>/status.json` |
| Logos | `.../shared/logos/<slug>-v<N>.png` (a new version gets a new name) |

Fields of each feed are listed in its `feeds/<feed>/feed.yml`; what each
type publishes is in [docs/FEED_SETTINGS.md](docs/FEED_SETTINGS.md).

## Layout

```
engine/          the code (collect, apply, build, validate, logos, orgs, correct) and its test
docs/            REVIEW.md (Claude's daily procedure), FEED_SETTINGS.md
feeds/<feed>/    feed.yml (settings), records.json (master data), corrections.json (Sean's,
                 always win), changes/ (Claude's change files, then applied/ and rejected/),
                 inbox.json and seen.json (waiting list), feed.json and feed.csv (published),
                 columns.json, worklist.json, status.json, state.json
shared/          organizations.json (names, short names, charters, logos for every feed), logos/
tracker/         the tracker page (GitHub Pages)
.github/         workflows: publish, collect, health, logos
```

## Corrections

Tell Claude, or run
`python3 engine/correct.py <feed> --id <id> --set field=value --note "why"`
(or `--remove` to hide an item), then commit `corrections.json`. A
correction always wins over research and is never overwritten.

## One-time setup

- Settings > Pages: deploy from branch `main`, folder `/ (root)`. This serves
  the tracker page.
- Issues must be on (they are by default) for health alerts.

Run the engine's test with `python3 engine/tests/test_engine.py`.
