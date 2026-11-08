#!/usr/bin/env python3
"""Build the Rawaked public snapshot (snapshot.json for GitHub Pages).

Reads the public collections via the unauthenticated Firestore REST API
(same pattern as the daily NUMBERS report), applies the SAME filtering
rules as the daily report, trims every document to an explicit field
whitelist (never phones, never ownerUid, never codeHash, never repName),
computes the six counts, and writes ONE plain-JSON snapshot.

The snapshot is the read-path for public browsing:
  * GitHub Actions (.github/workflows/rebuild-snapshot.yml) runs this
    script every 30 minutes and publishes snapshot.json to GitHub Pages;
  * the web app (web/assets/data.js, snapshot mode) loads the boards
    from that ONE file (0 Firestore reads) instead of ~200 reads;
  * the daily NUMBERS report derives its counts from the same file.

This file is a verbatim copy of the verified transform in
~/workspace/libya-med-exchange/tools/build_public_snapshot.py
(the Firestore-document twin) except for this docstring and the size
note below — the filtering rules, whitelists and counts are IDENTICAL.

Usage:
  python3 build_snapshot.py --out public/snapshot.json
      # live REST read (needs quota headroom; ~225 reads)
  python3 build_snapshot.py --from-backup-dir <dir> --out /tmp/snapshot.json
      # offline: build from backup JSON files (facilities.json etc. in
      # REST document format) — used to verify the transform without
      # spending quota, e.g. against the 2026-11-06 backup.
  python3 build_snapshot.py --from-backup-dir <dir> --expect-counts <counts.json>
      # also assert the six counts equal a reference counts file.

Exit code 0 on success; non-zero on any read/transform failure.
Prints counts only (no personal data) for the workflow log.
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PROJECT = "libya-med-exchange"
BASE = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"
COLLECTIONS = ["facilities", "shortages", "surpluses", "exchange_requests", "drug_aliases"]

# Explicit field whitelists. Anything not listed is DROPPED — this is
# what guarantees no phone number, no ownerUid, no codeHash and no
# repName can ever reach the public snapshot, whatever a document holds.
FIELDS = {
    "facilities": ["name", "city", "kind", "ord"],
    "shortages": ["drugId", "customName", "quantity", "urgency", "category",
                  "status", "notes", "facilityId", "createdAt", "seq"],
    "surpluses": ["drugId", "customName", "quantity", "expiry", "batch",
                  "category", "status", "notes", "facilityId", "createdAt", "seq"],
    "exchange_requests": ["drugId", "customName", "quantity", "type", "status",
                          "providerFacilityId", "requesterFacilityId",
                          "surplusId", "shortageId", "category", "ref",
                          "createdAt", "seq", "notes"],
    "drug_aliases": ["drugId", "alias", "ord", "seq", "source"],
}


def rest_value(v):
    """Convert one Firestore REST typed value to a plain Python value."""
    if not isinstance(v, dict):
        return v
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        try:
            return int(v["integerValue"])
        except (TypeError, ValueError):
            return v["integerValue"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "booleanValue" in v:
        return bool(v["booleanValue"])
    if "nullValue" in v:
        return None
    if "timestampValue" in v:
        return v["timestampValue"]
    if "arrayValue" in v:
        return [rest_value(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return {k: rest_value(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def rest_doc_to_plain(doc):
    out = {"id": doc["name"].split("/")[-1]}
    for key, val in (doc.get("fields") or {}).items():
        out[key] = rest_value(val)
    return out


def fetch_collection(col):
    docs = []
    url = f"{BASE}/{col}?pageSize=300"
    while url:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = json.load(resp)
        if "error" in data:
            raise RuntimeError(f"{col}: {data['error'].get('message', data['error'])}")
        docs.extend(data.get("documents", []))
        tok = data.get("nextPageToken")
        url = f"{BASE}/{col}?pageSize=300&pageToken={tok}" if tok else None
    return [rest_doc_to_plain(d) for d in docs]


def load_backup_dir(d):
    docs = {}
    for col in COLLECTIONS:
        path = os.path.join(d, col + ".json")
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and "error" in raw:
            raise RuntimeError(f"{col}: backup file holds an error body")
        docs[col] = [rest_doc_to_plain(x) for x in raw]
    return docs


def non_empty_str(v):
    return isinstance(v, str) and v.strip() != ""


def build(docs, generated_at):
    """Apply the daily-report filters + whitelists; return the snapshot."""
    live_fac_ids = set()
    facilities = []
    for f in docs["facilities"]:
        if not non_empty_str(f.get("name")):
            continue
        live_fac_ids.add(f["id"])
        row = {"id": f["id"]}
        for k in FIELDS["facilities"]:
            if k in f and f[k] is not None:
                row[k] = f[k]
        facilities.append(row)

    def trim(coll, items):
        out = []
        for d in items:
            row = {"id": d["id"]}
            for k in FIELDS[coll]:
                if k in d and d[k] is not None:
                    row[k] = d[k]
            out.append(row)
        return out

    shortages = trim("shortages", [d for d in docs["shortages"] if d.get("status") == "active"])
    surpluses = trim("surpluses", [d for d in docs["surpluses"] if d.get("status") == "active"])

    requests = []
    for d in docs["exchange_requests"]:
        if d.get("status") == "cancelled":
            continue
        fac_ids = [d.get("providerFacilityId"), d.get("requesterFacilityId")]
        fac_ids = [x for x in fac_ids if non_empty_str(x)]
        if not fac_ids:
            continue  # test/seed doc with no facility at all
        if any(x not in live_fac_ids for x in fac_ids):
            continue  # dangling reference to a deleted/unknown facility
        requests.append(d)
    requests = trim("exchange_requests", requests)

    aliases = trim("drug_aliases", [
        d for d in docs["drug_aliases"]
        if non_empty_str(d.get("drugId")) and non_empty_str(d.get("alias"))
    ])

    counts = {
        "facilities": len(facilities),
        "surpluses": len(surpluses),
        "shortages": len(shortages),
        "exchanges": len(requests),
        "completed": sum(1 for r in requests if r.get("status") in ("received", "completed")),
        "pending": sum(1 for r in requests if r.get("status") == "pending"),
    }
    tripoli = generated_at.astimezone(ZoneInfo("Africa/Tripoli"))
    return {
        "version": 1,
        "generatedAt": generated_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "generatedAtTripoli": tripoli.strftime("%Y-%m-%d %H:%M") + " Tripoli",
        "counts": counts,
        "facilities": facilities,
        "shortages": shortages,
        "surpluses": surpluses,
        "requests": requests,
        "aliases": aliases,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--from-backup-dir")
    ap.add_argument("--expect-counts")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    if args.from_backup_dir:
        docs = load_backup_dir(args.from_backup_dir)
        print("source: backup dir " + args.from_backup_dir)
    else:
        docs = {}
        for col in COLLECTIONS:
            docs[col] = fetch_collection(col)
            print(f"{col}: {len(docs[col])} docs")
    snap = build(docs, now)
    blob = json.dumps(snap, ensure_ascii=False)
    if len(blob.encode("utf-8")) > 1_000_000:
        print("WARNING: snapshot exceeds 1MB (unexpected for this data)", file=sys.stderr)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(blob)
    print("counts:", json.dumps(snap["counts"], ensure_ascii=False))
    print(f"snapshot bytes: {len(blob.encode('utf-8'))} -> {args.out}")
    if args.expect_counts:
        with open(args.expect_counts, encoding="utf-8") as fh:
            ref = json.load(fh)
        got = snap["counts"]
        mism = {k: (got.get(k), ref.get(k)) for k in
                ("facilities", "surpluses", "shortages", "exchanges", "completed", "pending")
                if got.get(k) != ref.get(k)}
        if mism:
            print("COUNT MISMATCH vs reference:", mism, file=sys.stderr)
            return 1
        print("counts match reference", args.expect_counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
