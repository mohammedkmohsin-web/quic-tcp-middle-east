#!/usr/bin/env python3
"""
===============================================================================
 step4_diurnal.py
 RIPE Atlas: how latency towards the Erbil anchor varies over a week
 Project: Performance of QUIC and TCP under Measured Middle Eastern Conditions
===============================================================================

 The question this answers
 -------------------------
 Step 2 collapsed 24 hours into a single number per country. The UAE came out
 at 181 ms. But is that 181 ms all day, or 150 ms at dawn and 220 ms in the
 evening? Networks congest at peak hours, and a single median hides that.

 Two things depend on the answer.

 First, the emulation parameters. If the spread is wide you need a peak
 profile and an off peak profile. If it is narrow, one number is defensible
 and you can say so with evidence.

 Second, and more important: step 2 found essentially zero packet loss. That
 is suspicious. Real networks drop packets when congested. It is possible the
 24 hour window happened to miss the busy hours. A full week settles it. If
 loss appears at peak, the study design changes. If it stays at zero, the
 claim that regional impairment is latency rather than loss becomes much
 stronger, because you looked for loss and did not find it.

 Why this is not just step 2 with a bigger number
 ------------------------------------------------
 Step 2 pooled every sample into one statistic. This script keeps the
 timestamp, converts it to local Iraqi time, and reports each hour of the day
 separately. That is what makes the daily pattern visible.

 Volume warning
 --------------
 The ping measurement runs across roughly a thousand probes every four
 minutes. A week of that is millions of records and cannot be downloaded.
 This script therefore selects a small panel of probes and walks through the
 week one day at a time.

 Run it:
     pip install requests
     python step4_diurnal.py

 It writes:
     diurnal_hourly.csv     per country, per hour of day
     diurnal_samples.csv    per probe, per day, for spot checks
===============================================================================
"""

import csv
import math
import statistics as stats
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    print("Install requests first:  pip install requests")
    sys.exit(1)


API = "https://atlas.ripe.net/api/v2"

# Ping IPv4 towards the Erbil anchor, from step 2
MEASUREMENT_ID = 31559013

# Countries to follow. Neighbours plus European and North American controls.
COUNTRIES = ["AE", "SA", "IR", "TR", "DE", "NL", "US"]

# Probes per country. Keep this small. Three is enough to see a pattern
# and keeps the download manageable.
PROBES_PER_COUNTRY = 3

DAYS_BACK = 7

# Iraq observes UTC+3 with no daylight saving, so the offset is constant.
IRAQ_UTC_OFFSET = 3

session = requests.Session()
session.headers.update({"User-Agent": "academic-research-script"})


# =============================================================================
# Utilities
# =============================================================================

def get(url, params=None, tries=3):
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=90)
            if r.status_code == 200:
                return r.json()
            print(f"      HTTP {r.status_code}")
        except Exception as e:
            print(f"      attempt {attempt + 1}: {e}")
        time.sleep(3)
    return None


def median(v):
    return stats.median(v) if v else None


def pct(v, p):
    if not v:
        return None
    s = sorted(v)
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def save_csv(rows, name):
    if not rows:
        print(f"  nothing to write to {name}")
        return
    with open(name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  saved {len(rows)} rows to {name}")


def local_hour(unix_ts):
    """Hour of day in Iraqi local time."""
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    return (dt + timedelta(hours=IRAQ_UTC_OFFSET)).hour


# =============================================================================
# Part 1 -- build the probe panel
# =============================================================================

def build_panel():
    print("=" * 74)
    print("PART 1  Selecting a probe panel")
    print("=" * 74)

    panel = {}
    for cc in COUNTRIES:
        d = get(f"{API}/probes/",
                {"country_code": cc, "status": 1, "page_size": 50})
        if not d:
            continue
        ids = [p["id"] for p in d.get("results", [])][:PROBES_PER_COUNTRY]
        for pid in ids:
            panel[pid] = cc
        print(f"  {cc}: using probes {ids}")
        time.sleep(0.2)

    print(f"\n  {len(panel)} probes across {len(COUNTRIES)} countries")
    print("  A small panel is deliberate. It keeps the download feasible")
    print("  and the daily pattern is a property of the path, not of how")
    print("  many probes you point at it.")
    return panel


# =============================================================================
# Part 2 -- walk the week one day at a time
# =============================================================================

def collect_week(panel):
    print("\n" + "=" * 74)
    print(f"PART 2  Downloading {DAYS_BACK} days, one day per request")
    print("=" * 74)

    ids = ",".join(str(p) for p in panel)
    now = int(time.time())
    samples = []          # (country, probe, unix_ts, rtt, sent, rcvd)

    for day in range(DAYS_BACK):
        stop = now - day * 86400
        start = stop - 86400
        label = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"\n  day {day + 1}/{DAYS_BACK}  starting {label}")

        results = get(f"{API}/measurements/{MEASUREMENT_ID}/results/",
                      {"start": start, "stop": stop,
                       "probe_ids": ids, "format": "json"})
        if not results:
            print("    no data for this day")
            continue

        added = 0
        for r in results:
            pid = r.get("prb_id")
            cc = panel.get(pid)
            if cc is None:
                continue
            ts = r.get("timestamp")
            sent = r.get("sent") or 0
            rcvd = r.get("rcvd") or 0
            rtts = [p["rtt"] for p in r.get("result", [])
                    if p.get("rtt") is not None]
            best = min(rtts) if rtts else None
            samples.append((cc, pid, ts, best, sent, rcvd))
            added += 1

        print(f"    {added} records")
        time.sleep(1)

    print(f"\n  {len(samples)} samples collected in total")
    return samples


# =============================================================================
# Part 3 -- the daily pattern
# =============================================================================

def analyse(samples):
    print("\n" + "=" * 74)
    print("PART 3  Latency by hour of day, Iraqi local time")
    print("=" * 74)

    hourly = defaultdict(list)        # (country, hour) -> rtts
    loss_by_hour = defaultdict(lambda: [0, 0])
    overall = defaultdict(list)

    for cc, pid, ts, rtt, sent, rcvd in samples:
        if ts is None:
            continue
        h = local_hour(ts)
        loss_by_hour[(cc, h)][0] += sent
        loss_by_hour[(cc, h)][1] += rcvd
        if rtt is not None:
            hourly[(cc, h)].append(rtt)
            overall[cc].append(rtt)

    rows = []
    for cc in COUNTRIES:
        if cc not in overall:
            continue

        base = median(overall[cc])
        print(f"\n  {cc}   week median {base:.1f} ms")
        print("  hour  samples   median   p95     loss%   vs median")
        print("  " + "-" * 58)

        peak_h, peak_v, quiet_h, quiet_v = None, None, None, None

        for h in range(24):
            vals = hourly.get((cc, h), [])
            sent, rcvd = loss_by_hour[(cc, h)]
            loss = 100.0 * (sent - rcvd) / sent if sent else None
            if not vals:
                continue

            med = median(vals)
            delta = med - base
            bar = "+" * min(int(max(delta, 0) / 2), 24)

            if peak_v is None or med > peak_v:
                peak_h, peak_v = h, med
            if quiet_v is None or med < quiet_v:
                quiet_h, quiet_v = h, med

            print(f"  {h:>4}  {len(vals):>7}  {med:>7.1f}  "
                  f"{pct(vals, 95):>6.1f}  {(f'{loss:.2f}' if loss is not None else '   -'):>6}"
                  f"   {bar}")

            rows.append({
                "country": cc,
                "hour_local": h,
                "samples": len(vals),
                "rtt_median": round(med, 2),
                "rtt_p95": round(pct(vals, 95), 2),
                "loss_pct": round(loss, 3) if loss is not None else None,
                "delta_vs_week_median": round(delta, 2),
            })

        if peak_v and quiet_v:
            swing = peak_v - quiet_v
            ratio = peak_v / quiet_v
            print(f"\n    busiest hour {peak_h:02d}:00 at {peak_v:.1f} ms")
            print(f"    quietest hour {quiet_h:02d}:00 at {quiet_v:.1f} ms")
            print(f"    swing {swing:.1f} ms, ratio {ratio:.2f}")
            if ratio < 1.10:
                print("    Flat. One emulation value is defensible here.")
            elif ratio < 1.30:
                print("    Mild. Note it, but one value still works.")
            else:
                print("    Wide. Build separate peak and off peak profiles.")

    return rows


def loss_verdict(samples):
    print("\n" + "=" * 74)
    print("PART 4  Did loss appear anywhere in the week")
    print("=" * 74)

    per_country = defaultdict(lambda: [0, 0])
    worst = defaultdict(float)

    for cc, pid, ts, rtt, sent, rcvd in samples:
        per_country[cc][0] += sent
        per_country[cc][1] += rcvd
        if sent:
            l = 100.0 * (sent - rcvd) / sent
            if l > worst[cc]:
                worst[cc] = l

    print("\n  country   overall loss%   worst single sample%")
    print("  " + "-" * 48)
    any_loss = False
    for cc in COUNTRIES:
        sent, rcvd = per_country.get(cc, [0, 0])
        if not sent:
            continue
        overall = 100.0 * (sent - rcvd) / sent
        if overall > 0.5:
            any_loss = True
        print(f"  {cc:<9} {overall:>12.3f}   {worst[cc]:>18.1f}")

    print()
    if any_loss:
        print("  Loss is present. Include it in the emulation as a measured")
        print("  condition, not only as a sensitivity sweep.")
    else:
        print("  Loss stays near zero across the whole week. This supports")
        print("  the claim that the regional impairment is latency rather")
        print("  than loss. State in the paper that you looked for loss over")
        print("  seven days and did not find it. A negative result you")
        print("  searched for carries more weight than one you assumed.")


# =============================================================================

def main():
    print(f"\nDiurnal analysis, measurement {MEASUREMENT_ID}")
    print(f"target: Erbil anchor    date: {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"times shown in Iraqi local time, UTC+{IRAQ_UTC_OFFSET}\n")

    panel = build_panel()
    if not panel:
        print("No probes selected.")
        return

    samples = collect_week(panel)
    if not samples:
        print("\nNo samples. Check that these probes take part in "
              f"measurement {MEASUREMENT_ID}.")
        return

    rows = analyse(samples)
    loss_verdict(samples)

    save_csv(rows, "diurnal_hourly.csv")
    save_csv([{"country": c, "probe_id": p, "timestamp": t,
               "rtt_min": r, "sent": s, "rcvd": v}
              for c, p, t, r, s, v in samples], "diurnal_samples.csv")

    print("\n" + "=" * 74)
    print("Done. diurnal_hourly.csv gives you one figure for the paper:")
    print("round trip time against hour of day, one line per country.")
    print("=" * 74 + "\n")


if __name__ == "__main__":
    main()
