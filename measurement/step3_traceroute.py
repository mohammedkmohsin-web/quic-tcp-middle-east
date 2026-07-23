#!/usr/bin/env python3
"""
===============================================================================
 step3_traceroute.py
 RIPE Atlas: AS-level paths towards the Iraqi anchor
 Project: Performance of QUIC and TCP under Measured Middle Eastern Conditions
===============================================================================

 Why this script exists
 ----------------------
 Step 2 showed that traffic from the Gulf reaches Erbil more slowly than
 traffic from North America. That is strong evidence of detouring, but it is
 still indirect. We infer the detour from the delay.

 This script removes the inference. It reads traceroute results, resolves each
 hop to its autonomous system, and prints the actual sequence of networks a
 packet crosses. If a packet from Dubai passes through a European carrier on
 its way to Erbil, you will see that carrier by name.

 That is the difference between "the timing suggests" and "the path shows".

 Run it:
     pip install requests
     python step3_traceroute.py

 It writes:
     as_paths.csv          one row per traceroute, with the AS path
     hop_details.csv       every hop, with ASN, holder, and RTT
===============================================================================
"""

import csv
import sys
import time
from collections import defaultdict, OrderedDict

try:
    import requests
except ImportError:
    print("Install requests first:  pip install requests")
    sys.exit(1)


ATLAS = "https://atlas.ripe.net/api/v2"
STAT = "https://stat.ripe.net/data"

# Traceroute IPv4 towards the Erbil anchor, from step 2
MEASUREMENT_ID = 31559012

# Source countries to examine. Neighbours first, then European and North
# American controls. Keep this list short: every extra probe costs lookups.
COUNTRIES = ["AE", "SA", "IR", "TR", "KW", "JO", "DE", "NL", "US"]

MAX_PROBES_PER_COUNTRY = 2
HOURS_BACK = 3               # a short window is enough, paths are stable

session = requests.Session()
session.headers.update({"User-Agent": "academic-research-script"})

_asn_of_ip = {}
_holder_of_asn = {}


# =============================================================================
# Utilities
# =============================================================================

def get(url, params=None, tries=3, quiet=False):
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=45)
            if r.status_code == 200:
                return r.json()
            if not quiet:
                print(f"      HTTP {r.status_code}")
        except Exception as e:
            if not quiet:
                print(f"      attempt {attempt + 1}: {e}")
        time.sleep(1.5)
    return None


def is_private(ip):
    """Skip addresses that carry no routing information."""
    if not ip or ":" in ip:
        return True
    parts = ip.split(".")
    if len(parts) != 4:
        return True
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return True
    if a == 10 or a == 127:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    if a == 100 and 64 <= b <= 127:
        return True
    return False


def asn_for_ip(ip):
    """Ask RIPE Stat which autonomous system announces this address."""
    if ip in _asn_of_ip:
        return _asn_of_ip[ip]
    d = get(f"{STAT}/network-info/data.json", {"resource": ip}, quiet=True)
    asn = None
    if d:
        asns = ((d.get("data") or {}).get("asns") or [])
        if asns:
            asn = str(asns[0])
    _asn_of_ip[ip] = asn
    time.sleep(0.12)
    return asn


def holder_for_asn(asn):
    """Human readable name of the network operator."""
    if asn is None:
        return None
    if asn in _holder_of_asn:
        return _holder_of_asn[asn]
    d = get(f"{STAT}/as-overview/data.json", {"resource": f"AS{asn}"},
            quiet=True)
    name = None
    if d:
        name = (d.get("data") or {}).get("holder")
    if name:
        name = name.split(",")[0].strip()[:38]
    _holder_of_asn[asn] = name
    time.sleep(0.12)
    return name


def save_csv(rows, filename):
    if not rows:
        print(f"  nothing to write to {filename}")
        return
    with open(filename, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  saved {len(rows)} rows to {filename}")


# =============================================================================
# Part 1 -- choose source probes
# =============================================================================

def pick_probes():
    print("=" * 74)
    print("PART 1  Selecting source probes")
    print("=" * 74)

    chosen = {}
    for cc in COUNTRIES:
        d = get(f"{ATLAS}/probes/",
                {"country_code": cc, "status": 1, "page_size": 50})
        if not d:
            continue
        ids = [p["id"] for p in d.get("results", [])][:MAX_PROBES_PER_COUNTRY]
        for pid in ids:
            chosen[pid] = cc
        print(f"  {cc}: {len(d.get('results', []))} connected, using {ids}")
        time.sleep(0.2)

    print(f"\n  {len(chosen)} probes selected")
    return chosen


# =============================================================================
# Part 2 -- fetch traceroutes
# =============================================================================

def fetch_traceroutes(probe_country):
    print("\n" + "=" * 74)
    print(f"PART 2  Traceroute results from measurement {MEASUREMENT_ID}")
    print("=" * 74)

    stop = int(time.time())
    start = stop - HOURS_BACK * 3600
    ids = ",".join(str(p) for p in probe_country)

    results = get(f"{ATLAS}/measurements/{MEASUREMENT_ID}/results/",
                  {"start": start, "stop": stop,
                   "probe_ids": ids, "format": "json"})

    if not results:
        print("  no results. Widen HOURS_BACK, or check that these probes")
        print("  take part in this measurement.")
        return {}

    # keep only the most recent traceroute per probe
    latest = {}
    for r in results:
        pid = r.get("prb_id")
        if pid not in latest or r.get("timestamp", 0) > latest[pid].get("timestamp", 0):
            latest[pid] = r

    print(f"  {len(results)} records, {len(latest)} distinct probes")
    return latest


# =============================================================================
# Part 3 -- resolve hops to autonomous systems
# =============================================================================

def analyse_path(trace, cc):
    """Turn one traceroute into an AS level path."""
    hops_out = []
    as_sequence = OrderedDict()

    for hop in trace.get("result", []):
        num = hop.get("hop")
        ip, rtt = None, None
        for reply in hop.get("result", []):
            if reply.get("from"):
                ip = reply["from"]
                if reply.get("rtt") is not None:
                    rtt = reply["rtt"] if rtt is None else min(rtt, reply["rtt"])
        if ip is None:
            hops_out.append({"hop": num, "ip": "*", "asn": None,
                             "holder": None, "rtt": None})
            continue

        asn = None if is_private(ip) else asn_for_ip(ip)
        holder = holder_for_asn(asn) if asn else None
        hops_out.append({"hop": num, "ip": ip, "asn": asn,
                         "holder": holder,
                         "rtt": round(rtt, 2) if rtt is not None else None})
        if asn and asn not in as_sequence:
            as_sequence[asn] = holder

    return hops_out, as_sequence


def report(probe_country, traces):
    print("\n" + "=" * 74)
    print("PART 3  AS level paths towards Erbil")
    print("=" * 74)

    path_rows, hop_rows = [], []
    by_country = defaultdict(list)

    for pid, trace in sorted(traces.items(),
                             key=lambda kv: probe_country.get(kv[0], "")):
        cc = probe_country.get(pid, "??")
        hops, as_seq = analyse_path(trace, cc)

        rtts = [h["rtt"] for h in hops if h["rtt"] is not None]
        final = round(rtts[-1], 1) if rtts else None

        print(f"\n  {cc}  probe {pid}   final RTT {final} ms")
        print("  " + "-" * 68)
        prev = None
        for h in hops:
            jump = ""
            if h["rtt"] is not None and prev is not None:
                d = h["rtt"] - prev
                if d > 25:
                    jump = f"   <== +{d:.0f} ms long haul"
            if h["rtt"] is not None:
                prev = h["rtt"]
            name = h["holder"] or ("private" if h["ip"] != "*" and
                                   is_private(h["ip"]) else "")
            asn = f"AS{h['asn']}" if h["asn"] else ""
            rtt = f"{h['rtt']:.1f}" if h["rtt"] is not None else "  *  "
            print(f"  {h['hop']:>3}  {h['ip']:<16} {rtt:>7}  "
                  f"{asn:<10} {name}{jump}")

            hop_rows.append({"probe_id": pid, "country": cc, **h})

        chain = " > ".join(f"AS{a}" for a in as_seq)
        names = " > ".join((n or f"AS{a}") for a, n in as_seq.items())
        print(f"\n       AS path: {chain}")

        path_rows.append({
            "probe_id": pid,
            "source_country": cc,
            "final_rtt_ms": final,
            "as_hops": len(as_seq),
            "as_path": chain,
            "operator_path": names,
        })
        by_country[cc].append(names)

    save_csv(path_rows, "as_paths.csv")
    save_csv(hop_rows, "hop_details.csv")

    print("\n" + "=" * 74)
    print("How to read this")
    print("=" * 74)
    print("""
  Look at the neighbouring countries first. Follow the operator names down
  the path and ask one question: does the packet reach a large international
  carrier before it reaches Iraq?

  If a trace from the Gulf passes through a European or global transit
  provider, that is the detour, observed rather than inferred. Compare it
  with the Turkish trace, which step 2 suggests takes a shorter route.

  The long haul markers show where a single link adds more than 25 ms. That
  is usually the intercontinental leg. Note which operator sits on each side
  of it, because that pair identifies the point where regional traffic
  leaves the region.
""")


# =============================================================================

def main():
    print(f"\nTraceroute analysis towards the Erbil anchor")
    print(f"measurement {MEASUREMENT_ID}   "
          f"date {time.strftime('%Y-%m-%d %H:%M')}\n")

    probe_country = pick_probes()
    if not probe_country:
        print("No probes selected.")
        return

    traces = fetch_traceroutes(probe_country)
    if not traces:
        return

    print(f"\n  resolving hops to autonomous systems, this takes a minute")
    report(probe_country, traces)

    print(f"  cached {len(_asn_of_ip)} addresses and "
          f"{len(_holder_of_asn)} operators\n")


if __name__ == "__main__":
    main()
