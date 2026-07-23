#!/usr/bin/env python3
"""
===============================================================================
 step2_anchor.py
 RIPE Atlas: latency and path inflation towards the Iraqi anchor
 Project: Performance of QUIC and TCP under Measured Middle Eastern Conditions
===============================================================================

 The anchor iq-abl-as21277.anchors.atlas.ripe.net sits in Erbil on AS21277.
 Probes around the world measure it continuously. That data is public and
 costs no credits.

 What this script produces:
   1. A list of the live measurements aimed at the anchor
   2. Round trip time statistics from every source probe
   3. Path inflation: measured RTT divided by the physical minimum

 Path inflation is the number this study rests on. A ratio near 1 means the
 packets travel close to a straight line. A ratio above 2 means they detour.
 If traffic from a neighbouring country shows a high ratio, that is direct
 evidence of circuitous routing, measured rather than cited.

 Run it:
     pip install requests
     python step2_anchor.py

 It writes:
     anchor_measurements.csv    the six live measurements
     anchor_rtt_stats.csv       one row per source probe, with inflation
===============================================================================
"""

import csv
import math
import sys
import time
from collections import defaultdict

try:
    import requests
except ImportError:
    print("Install the requests library first:  pip install requests")
    sys.exit(1)


API = "https://atlas.ripe.net/api/v2"

# The Iraqi anchor, from step 1
ANCHOR_IP = "130.193.166.22"
ANCHOR_FQDN = "iq-abl-as21277.anchors.atlas.ripe.net"
ANCHOR_LAT = 36.2375          # Erbil
ANCHOR_LON = 44.0085

HOURS_BACK = 24               # raise to 168 for a full week once this works

session = requests.Session()
session.headers.update({"User-Agent": "academic-research-script"})

_probe_cache = {}


# =============================================================================
# Utilities
# =============================================================================

def get(url, params=None):
    for attempt in (1, 2, 3):
        try:
            r = session.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            print(f"    HTTP {r.status_code}")
        except Exception as e:
            print(f"    attempt {attempt}: {e}")
        time.sleep(2)
    return None


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def min_possible_rtt_ms(km):
    """
    Light in fibre covers about two thirds of its vacuum speed, and a round
    trip is twice the distance. Nothing can go faster than this.
    """
    return 2.0 * km / (299.792 * 2.0 / 3.0)


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def save_csv(rows, name):
    if not rows:
        print(f"    nothing to save in {name}")
        return
    with open(name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"    saved {len(rows)} rows to {name}")


def probe_info(pid):
    """Look up a probe once and remember it."""
    if pid in _probe_cache:
        return _probe_cache[pid]
    d = get(f"{API}/probes/{pid}/") or {}
    geom = d.get("geometry") or {}
    c = geom.get("coordinates") or []
    info = {
        "country": d.get("country_code"),
        "asn": d.get("asn_v4"),
        "lat": c[1] if len(c) > 1 else None,
        "lon": c[0] if len(c) > 0 else None,
    }
    _probe_cache[pid] = info
    time.sleep(0.05)
    return info


# =============================================================================
# Part 1 -- the measurements
# =============================================================================

def list_measurements():
    print("=" * 74)
    print("PART 1  Live measurements aimed at the anchor")
    print("=" * 74)

    data = get(f"{API}/measurements/",
               {"target": ANCHOR_FQDN, "status": 2, "page_size": 50})

    # Falling back to the raw address returns IPv4 work only, so it is the
    # narrower query. Use it only if the hostname lookup comes back empty.
    if not data or not data.get("results"):
        print("  hostname query returned nothing, trying the IP address")
        data = get(f"{API}/measurements/",
                   {"target_ip": ANCHOR_IP, "status": 2, "page_size": 50})

    if not data:
        print("  request failed")
        return []

    rows = []
    for m in data.get("results", []):
        rows.append({
            "id": m.get("id"),
            "type": m.get("type"),
            "af": m.get("af"),
            "interval_s": m.get("interval"),
            "participants": m.get("participant_count"),
            "description": (m.get("description") or "")[:45],
        })

    print(f"\n  {data.get('count')} live measurements\n")
    print("  id           type        af  every  probes  description")
    print("  " + "-" * 68)
    for r in rows:
        print(f"  {r['id']:<12} {r['type']:<11} {str(r['af']):<3} "
              f"{str(r['interval_s']):<6} {str(r['participants']):<7} "
              f"{r['description']}")

    save_csv(rows, "anchor_measurements.csv")
    return rows


def pick_ping(rows):
    """Prefer an IPv4 ping with the widest participation."""
    pings = [r for r in rows if r["type"] == "ping" and r["af"] == 4]
    if not pings:
        pings = [r for r in rows if r["type"] == "ping"]
    if not pings:
        return None
    return max(pings, key=lambda r: r["participants"] or 0)


# =============================================================================
# Part 2 -- results
# =============================================================================

def analyse(measurement_id):
    print("\n" + "=" * 74)
    print(f"PART 2  Results from measurement {measurement_id}, "
          f"last {HOURS_BACK} hours")
    print("=" * 74)

    stop = int(time.time())
    start = stop - HOURS_BACK * 3600

    results = get(f"{API}/measurements/{measurement_id}/results/",
                  {"start": start, "stop": stop, "format": "json"})
    if not results:
        print("  no results returned. Try increasing HOURS_BACK.")
        return []

    print(f"\n  {len(results)} raw records")

    rtts = defaultdict(list)
    sent_rcvd = defaultdict(lambda: [0, 0])

    for r in results:
        pid = r.get("prb_id")
        sent_rcvd[pid][0] += r.get("sent") or 0
        sent_rcvd[pid][1] += r.get("rcvd") or 0
        for packet in r.get("result", []):
            v = packet.get("rtt")
            if v is not None:
                rtts[pid].append(v)

    print(f"  {len(rtts)} source probes reported\n")

    rows = []
    for pid, values in rtts.items():
        values.sort()
        info = probe_info(pid)
        sent, rcvd = sent_rcvd[pid]

        median = pct(values, 50)
        row = {
            "probe_id": pid,
            "country": info["country"],
            "asn": info["asn"],
            "samples": len(values),
            "rtt_min": round(values[0], 2),
            "rtt_median": round(median, 2),
            "rtt_p95": round(pct(values, 95), 2),
            "jitter_iqr": round(pct(values, 75) - pct(values, 25), 2),
            "loss_pct": round(100.0 * (sent - rcvd) / sent, 2) if sent else None,
            "distance_km": None,
            "min_rtt_ms": None,
            "inflation": None,
        }

        if info["lat"] is not None and info["lon"] is not None:
            km = haversine_km(info["lat"], info["lon"], ANCHOR_LAT, ANCHOR_LON)
            floor = min_possible_rtt_ms(km)
            row["distance_km"] = round(km, 1)
            row["min_rtt_ms"] = round(floor, 2)
            if floor > 0.5:
                row["inflation"] = round(values[0] / floor, 2)

        rows.append(row)

    rows.sort(key=lambda r: (r["inflation"] is None, -(r["inflation"] or 0)))
    return rows


def report(rows):
    if not rows:
        return

    print("  Highest path inflation (measured minimum over physical floor)\n")
    print("  probe     cc   distance  floor   measured  inflation  loss%")
    print("  " + "-" * 66)
    for r in rows[:20]:
        if r["inflation"] is None:
            continue
        print(f"  {r['probe_id']:<9} {str(r['country']):<4} "
              f"{r['distance_km']:>8.0f}  {r['min_rtt_ms']:>6.1f}  "
              f"{r['rtt_min']:>8.1f}  {r['inflation']:>9.2f}  "
              f"{str(r['loss_pct']):>5}")

    by_country = defaultdict(list)
    for r in rows:
        if r["inflation"] is not None:
            by_country[r["country"]].append(r["inflation"])

    print("\n  Median inflation by source country (3 or more probes)\n")
    print("  country   probes   median inflation")
    print("  " + "-" * 40)
    ranked = sorted(by_country.items(),
                    key=lambda kv: -pct(sorted(kv[1]), 50))
    for cc, vals in ranked:
        if len(vals) < 3:
            continue
        print(f"  {str(cc):<9} {len(vals):<8} {pct(sorted(vals), 50):.2f}")

    print("\n  Reading this table:")
    print("    near 1.0  packets travel close to a straight line")
    print("    above 2.0 the path detours substantially")
    print("    Watch the neighbouring countries. A high ratio there is")
    print("    direct evidence of regional traffic leaving the region.")


# =============================================================================

def main():
    print(f"\nAnchor: {ANCHOR_FQDN}")
    print(f"Erbil, Iraq, AS21277, {ANCHOR_IP}")
    print(f"Date: {time.strftime('%Y-%m-%d %H:%M')}\n")

    measurements = list_measurements()
    if not measurements:
        return

    chosen = pick_ping(measurements)
    if not chosen:
        print("\nNo ping measurement among them. Inspect "
              "anchor_measurements.csv and set the id by hand.")
        return

    rows = analyse(chosen["id"])
    report(rows)
    save_csv(rows, "anchor_rtt_stats.csv")

    print("\n" + "=" * 74)
    print("Done. anchor_rtt_stats.csv is the raw material for Section III.")
    print("Once this works, raise HOURS_BACK to 168 for a full week.")
    print("=" * 74 + "\n")


if __name__ == "__main__":
    main()
