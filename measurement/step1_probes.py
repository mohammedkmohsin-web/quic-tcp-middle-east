#!/usr/bin/env python3
"""
===============================================================================
 step1_probes.py
 RIPE Atlas: Iraqi probe inventory
 Project: Performance of QUIC and TCP under Measured Middle Eastern Conditions
===============================================================================

 Run it:
     pip install requests
     python step1_probes.py

 No API key. No credits. It only reads public data.

 It writes two files next to itself:
     probes_IQ.csv     one row per Iraqi probe
     anchors_IQ.csv    one row per Iraqi anchor, if any exist

 Nothing here can fail destructively. Every network call is wrapped, so a
 timeout prints a warning and the script keeps going.
===============================================================================
"""

import csv
import sys
import time
from collections import defaultdict

try:
    import requests
except ImportError:
    print("The requests library is missing. Install it with:")
    print("    pip install requests")
    sys.exit(1)


API = "https://atlas.ripe.net/api/v2"
COUNTRY = "IQ"

session = requests.Session()
session.headers.update({"User-Agent": "academic-research-script"})


def get(url, params=None):
    """One GET request. Returns parsed JSON, or None on failure."""
    for attempt in (1, 2, 3):
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            print(f"    HTTP {r.status_code}")
        except Exception as e:
            print(f"    attempt {attempt} failed: {e}")
        time.sleep(2)
    return None


def coords_of(probe):
    """
    Pull latitude and longitude out of the GeoJSON geometry field.
    Inside coordinates the order is [longitude, latitude], which is the
    reverse of how people usually write it.
    """
    geom = probe.get("geometry") or {}
    c = geom.get("coordinates") or []
    lon = c[0] if len(c) > 0 else None
    lat = c[1] if len(c) > 1 else None
    return lat, lon


def save_csv(rows, filename):
    if not rows:
        print(f"    nothing to save in {filename}")
        return
    with open(filename, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"    saved {len(rows)} rows to {filename}")


# =============================================================================
# Part 1 -- probes
# =============================================================================

def collect_probes():
    print("=" * 70)
    print(f"PART 1  Connected probes in {COUNTRY}")
    print("=" * 70)

    data = get(f"{API}/probes/",
               {"country_code": COUNTRY, "status": 1, "page_size": 100})
    if not data:
        print("  could not reach the API")
        return []

    rows = []
    for p in data.get("results", []):
        lat, lon = coords_of(p)
        rows.append({
            "probe_id":  p.get("id"),
            "asn":       p.get("asn_v4"),
            "latitude":  lat,
            "longitude": lon,
            "is_anchor": p.get("is_anchor"),
            "status":    (p.get("status") or {}).get("name"),
        })

    print(f"\n  total reported by the API: {data.get('count')}")
    print(f"  retrieved on this page:    {len(rows)}\n")

    print("  probe        ASN       latitude   longitude  anchor")
    print("  " + "-" * 56)
    missing = 0
    for r in rows:
        lat = f"{r['latitude']:.4f}" if r["latitude"] is not None else "   ---  "
        lon = f"{r['longitude']:.4f}" if r["longitude"] is not None else "   ---  "
        if r["latitude"] is None:
            missing += 1
        mark = "yes" if r["is_anchor"] else ""
        print(f"  {r['probe_id']:<12} AS{r['asn']:<8} {lat:>9}  {lon:>9}   {mark}")

    if missing:
        print(f"\n  note: {missing} probe(s) publish no location. "
              "Their host chose to hide it.")

    # group by network operator
    by_asn = defaultdict(list)
    for r in rows:
        by_asn[r["asn"]].append(r["probe_id"])

    print(f"\n  {len(rows)} probes spread across {len(by_asn)} networks:")
    for asn, ids in sorted(by_asn.items(), key=lambda kv: -len(kv[1])):
        share = 100.0 * len(ids) / len(rows)
        print(f"    AS{asn:<10} {len(ids)} probe(s)  ({share:.0f}%)  {ids}")

    if len(by_asn) < len(rows):
        print("\n  Sampling note: some networks host more than one probe.")
        print("  Use one probe per network so no single operator dominates.")

    anchors = [r for r in rows if r["is_anchor"]]
    if anchors:
        print(f"\n  Anchor found: probe {anchors[0]['probe_id']}")
        print("  Anchors run continuous measurements to other anchors "
              "worldwide.")
        print("  That history is free to download and needs no credits.")

    save_csv(rows, "probes_IQ.csv")
    return rows


# =============================================================================
# Part 2 -- anchors
# =============================================================================

def collect_anchors():
    print("\n" + "=" * 70)
    print(f"PART 2  Anchors in {COUNTRY}")
    print("=" * 70)

    found = []
    url = f"{API}/anchors/"
    params = {"page_size": 100}
    pages = 0

    while url and pages < 30:
        data = get(url, params)
        params = None          # the next link already carries the query
        if not data:
            break
        for a in data.get("results", []):
            if a.get("country") == COUNTRY:
                found.append({
                    "anchor_id": a.get("id"),
                    "probe_id":  a.get("probe"),
                    "fqdn":      a.get("fqdn"),
                    "asn":       a.get("as_v4"),
                    "city":      a.get("city"),
                    "ip_v4":     a.get("ip_v4"),
                })
        url = data.get("next")
        pages += 1
        time.sleep(0.3)

    if not found:
        print("\n  No anchor registered under this country code.")
        print("  If part 1 reported an anchor probe, the anchor record may")
        print("  simply be filed under a different field. Not a problem.")
    else:
        for a in found:
            print(f"\n  {a['fqdn']}")
            print(f"    anchor id {a['anchor_id']}, probe {a['probe_id']}")
            print(f"    AS{a['asn']}, {a['city']}, {a['ip_v4']}")

    save_csv(found, "anchors_IQ.csv")
    return found


# =============================================================================
# Part 3 -- live measurements
# =============================================================================

def find_live_measurements(probe_rows):
    print("\n" + "=" * 70)
    print("PART 3  Ongoing measurements these probes take part in")
    print("=" * 70)

    seen = {}
    for r in probe_rows:
        pid = r["probe_id"]
        data = get(f"{API}/probes/{pid}/measurements/",
                   {"status": 2, "page_size": 50})
        if not data:
            continue
        for m in data.get("results", []):
            if m.get("type") not in ("ping", "traceroute"):
                continue
            seen[m.get("id")] = {
                "measurement_id": m.get("id"),
                "type":           m.get("type"),
                "target":         m.get("target"),
                "target_asn":     m.get("target_asn"),
                "interval_s":     m.get("interval"),
                "participants":   m.get("participant_count"),
                "description":    (m.get("description") or "")[:50],
            }
        time.sleep(0.3)

    rows = sorted(seen.values(),
                  key=lambda m: -(m.get("participants") or 0))

    if not rows:
        print("\n  None found through this endpoint.")
        print("  Fall back to the anchor: its mesh measurements are")
        print("  continuous, historical, and free.")
    else:
        print(f"\n  {len(rows)} live measurements\n")
        print("  id           type        participants  target")
        print("  " + "-" * 60)
        for m in rows[:25]:
            print(f"  {m['measurement_id']:<12} {m['type']:<11} "
                  f"{str(m['participants']):<13} {m['target']}")

    save_csv(rows, "live_measurements_IQ.csv")
    return rows


# =============================================================================

def main():
    print("\nRIPE Atlas inventory")
    print(f"country: {COUNTRY}    date: {time.strftime('%Y-%m-%d %H:%M')}\n")

    probes = collect_probes()
    if not probes:
        print("\nStopping: no probe data was retrieved.")
        return

    collect_anchors()
    find_live_measurements(probes)

    print("\n" + "=" * 70)
    print("Done.")
    print("Files written: probes_IQ.csv, anchors_IQ.csv, "
          "live_measurements_IQ.csv")
    print("Record today's date with these numbers. The probe population")
    print("changes over time, so the paper must state when it was sampled.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
