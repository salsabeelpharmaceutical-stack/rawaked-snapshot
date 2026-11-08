# rawaked-snapshot

Public data snapshot for the Rawaked (رواكد) medicines-exchange platform
— https://libya-med-exchange.web.app/

## What this repo does

A scheduled GitHub Actions workflow (`.github/workflows/rebuild-snapshot.yml`)
runs every 30 minutes:

1. `build_snapshot.py` reads the platform's five public Firestore
   collections through the **unauthenticated** Firestore REST API (the
   same reads the daily numbers report uses — about 170 documents per run).
2. It applies the same filtering rules as the daily report (live
   facilities only; active shortages/surpluses only; requests minus
   cancelled and minus dangling facility references) and trims every
   document to an explicit field whitelist.
3. It writes `public/snapshot.json` and deploys it to GitHub Pages.

The Rawaked web app loads its public boards from that ONE file
(0 Firestore reads per load) instead of ~200 reads, and the daily
numbers report derives its counts from it. If a run fails (for example
the Firestore daily quota is exhausted at that moment) the run fails
visibly, Pages keeps the previous file, and the next run retries.
The app falls back to live Firestore reads if the file is older than
2 hours.

## What this repo contains — and what it never contains

- **Only public board data:** facility names/cities, active shortages,
  active surpluses, exchange requests, drug aliases, and the six counts.
- **Never:** phone numbers, `ownerUid`, `codeHash`, `repName`,
  registration codes, or any credential. **No secrets of any kind**
  live in this repo — the Firestore reads are unauthenticated, so the
  workflow needs no token, key, or secret.
- The repo must stay **PUBLIC** (GitHub Pages on the free plan requires
  a public repository). That is safe here because everything published
  is already public on the platform itself.

## Files

- `build_snapshot.py` — the builder. A verbatim copy of the verified
  transform in the main project
  (`tools/build_public_snapshot.py`), except this docstring and the
  size note. Filtering rules, whitelists and counts are identical.
- `public/index.html` — a short human note served next to the JSON.
- `.github/workflows/rebuild-snapshot.yml` — the 30-minute schedule.

## Local test (no live reads)

```bash
python3 build_snapshot.py \
  --from-backup-dir <backup dir with facilities.json etc.> \
  --expect-counts <reference counts.json> \
  --out /tmp/snapshot.json
```

## Setup

See `SETUP-CHECKLIST.md` in the main project
(`~/workspace/libya-med-exchange/`) — it is not part of this repo.
