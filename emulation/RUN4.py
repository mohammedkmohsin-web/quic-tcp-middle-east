#!/usr/bin/env python3
"""
===============================================================================
 RUN.py
 Experiment runner for the three-arm bracketing design
 Project: Performance of QUIC and TCP under Measured Middle Eastern Conditions
===============================================================================

 This replaces run_experiments.py entirely. Delete that file.

 The earlier runner failed on every transfer because of an architectural
 mistake, not a bug. Mininet gives each host its own network namespace. The
 old runner started Caddy through systemd, in the root namespace, and started
 curl inside a Docker container, in a third namespace. Neither endpoint could
 see the address the other was using.

 GO3.py proved the correct arrangement. Both endpoints run inside the
 emulated hosts, on either side of the impaired link, which is the only
 arrangement in which the timings mean anything. This runner uses that
 arrangement and adds the full condition matrix.

 Usage

     sudo python3 RUN.py --smoke      three conditions, a few minutes
     sudo python3 RUN.py --all        the full matrix, several hours
     sudo python3 RUN.py --profile P4 one profile only

 Always start with --smoke.

 Statistics

 Timing noise on a virtual machine is one-sided: a scheduling delay can only
 make a transfer look slower, never faster than the emulated path permits.
 The minimum is therefore the least contaminated sample. Pilot runs made this
 concrete. Across three sessions the QUIC minimum held between 389 and 409 ms
 while the median moved from 442 to 931 ms, because the distribution is
 bimodal and the median lands on whichever cluster is larger that day.

 We report the minimum, with the count of runs near it as an honest indicator
 of session quality. This matches the treatment of path inflation in Section
 III-D, where the minimum round trip time is likewise the meaningful figure.
===============================================================================
"""

import argparse
import csv
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from mininet.net import Mininet
    from mininet.node import OVSKernelSwitch
    from mininet.link import TCLink
    from mininet.log import setLogLevel
except ImportError:
    print("Mininet is missing:  sudo apt install -y mininet")
    sys.exit(1)

try:
    from topology import PROFILES, buffer_packets
except ImportError:
    print("topology.py must be in this directory. Run from ~/quic")
    sys.exit(1)


# =============================================================================

CADDY_CONFIG = "/etc/caddy/Caddyfile"
WEB_ROOT = "/var/www/quictest"
SERVER_IP = "10.0.0.2"
H3_ROOT = "/opt/h3root"
H3_CURL = "/usr/local/bin/curl"

ARMS = ["quic", "tcp-cubic", "tcp-bbr"]
# The third element is the URL expression handed to curl. For W2 we use
# curl's own range syntax rather than listing a hundred URLs on the command
# line. Passing them individually produced a command line thousands of
# characters long and a transfer that took minutes instead of seconds.
WORKLOADS = {
    "W1": ("large.bin", 1,
           "10 MB transfer, congestion control dominates"),
    "W2": ("small/obj[000-099].bin", 100,
           "100 x 20 KB, exercises multiplexing"),
    "W3": ("tiny.bin", 1,
           "10 KB request, handshake dominates"),
}

# No single transfer should ever take this long. If one does, something has
# gone wrong and the run is abandoned rather than left to hang.
TRANSFER_TIMEOUT_S = 120

REPS_SMOKE = 10
REPS_FULL = 50

# Bottleneck bandwidth.
#
# This value was not chosen. It was measured.
#
# The paper first specified 50 Mbit/s. iperf reported 4.33 Mbit/s against that
# setting, and the emulated round trip time rose from 63 to 84 ms at the same
# moment. Lowering the setting to 10 Mbit/s changed nothing: the achieved rate
# stayed near 3 Mbit/s. When the delivered rate is independent of the
# configured rate, the constraint is not the emulated link. It is the host,
# which must move every packet through a software queue, a virtual switch and
# a guest kernel.
#
# So the bottleneck is set to what the machine actually delivers. The point is
# not to reach an impressive number. It is that the configured value and the
# measured value agree, because a bulk transfer measured on a link that fails
# to deliver its own setting describes the machine rather than the network.
#
# Three megabits is not an unrealistic figure. It is a plausible regional
# mobile or entry-level fixed line, and the study is about regions where such
# links are common. What must not happen is claiming fifty and delivering
# three.
#
# The handshake workload W3 is unaffected. Connection setup is governed by
# round trip time, not bandwidth, and a 10 KB object never approaches the
# capacity ceiling. Those results, already collected across all four profiles,
# remain valid without qualification.
DEFAULT_BW = 3
BW_SWEEP = (2, 3, 5)
MIN_ACCEPTABLE_BW_RATIO = 0.80   # measured capacity against configured
CLEAN_MARGIN = 1.15
CURL_FMT = r'%{http_code} %{time_starttransfer} %{time_total} %{size_download}'


def sh(cmd, timeout=120):
    import subprocess
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


# =============================================================================
# Environment
# =============================================================================

def preflight():
    if os.geteuid() != 0:
        print("Run with sudo:  sudo python3 RUN.py --smoke")
        sys.exit(1)

    print("Checking the environment")

    sh("systemctl stop caddy")
    sh("systemctl disable caddy")
    sh("pkill -f 'caddy run'")
    sh("mn -c")

    # UDP buffers. The kernel default is far too small for QUIC and its
    # effect is dramatic: raising it removed roughly two round trips from
    # every QUIC handshake in pilot runs.
    for key, val in [("net.core.rmem_max", 7500000),
                     ("net.core.wmem_max", 7500000),
                     ("net.core.rmem_default", 2500000),
                     ("net.core.wmem_default", 2500000)]:
        sh(f"sysctl -w {key}={val}")
    print("  UDP buffers raised")

    if not os.path.exists(f"{WEB_ROOT}/tiny.bin"):
        print(f"  missing test content. Run GO3.py first.")
        sys.exit(1)
    if not os.path.exists(f"{H3_ROOT}{H3_CURL}"):
        print(f"  missing HTTP/3 client. Run GO3.py first.")
        sys.exit(1)
    print("  content and HTTP/3 client present")


# =============================================================================
# Network
# =============================================================================

def build_net(profile_key, bw, loss, burst):
    p = PROFILES[profile_key]
    one_way = p["rtt_ms"] / 2.0
    q = buffer_packets(bw, p["rtt_ms"])
    lo = p["loss_pct"] if loss is None else loss

    setLogLevel("critical")
    net = Mininet(switch=OVSKernelSwitch, link=TCLink, controller=None,
                  autoSetMacs=True)
    client = net.addHost("client", ip="10.0.0.1/24")
    server = net.addHost("server", ip=f"{SERVER_IP}/24")
    s1 = net.addSwitch("s1", failMode="standalone")
    s2 = net.addSwitch("s2", failMode="standalone")
    net.addLink(client, s1)
    net.addLink(server, s2)

    args = dict(bw=bw, delay=f"{one_way:.2f}ms",
                jitter=f"{p['jitter_ms']:.2f}ms",
                max_queue_size=q, use_htb=True)
    if lo > 0:
        args["loss"] = lo
    net.addLink(s1, s2, cls=TCLink, **args)
    net.start()
    return net, client, server


def verify_capacity(client, server, target_bw):
    """
    Measure what the emulated link actually delivers.

    A configured bottleneck is a request, not a guarantee. When the host
    cannot keep up, the achieved rate falls well below the setting and the
    emulated delay rises at the same time. Any bulk transfer measured under
    those conditions describes the host, not the network.

    We therefore measure capacity before each group and report it. If the
    achieved rate falls too far below the target, the group is still run but
    the deficit is recorded, because a silent shortfall is far worse than a
    stated one.
    """
    server.cmd("pkill iperf; iperf -s -D > /dev/null 2>&1")
    time.sleep(1.5)
    out = client.cmd(f"timeout 15 iperf -c {SERVER_IP} -t 5 2>&1")
    server.cmd("pkill iperf")

    m = re.findall(r"([\d.]+)\s*Mbits/sec", out)
    if not m:
        return None, None
    achieved = float(m[-1])
    return achieved, achieved / target_bw


def verify_path(client, target_rtt):
    out = client.cmd(f"ping -c 10 -q {SERVER_IP}")
    m = re.search(r"= ([\d.]+)/", out)
    if not m:
        return None
    measured = float(m.group(1))
    drift = abs(measured - target_rtt) / target_rtt * 100
    return measured, drift


def start_server(server):
    server.cmd(f"XDG_DATA_HOME=/tmp/cd XDG_CONFIG_HOME=/tmp/cd "
               f"/usr/bin/caddy run --config {CADDY_CONFIG} "
               f"> /tmp/caddy.log 2>&1 &")
    for _ in range(25):
        time.sleep(1)
        if "443" in server.cmd("ss -lntu | grep 443 || true"):
            return True
    return False


# =============================================================================
# One transfer
# =============================================================================

def transfer(client, arm, workload):
    pattern, count, _ = WORKLOADS[workload]
    url = f"https://{SERVER_IP}/{pattern}"

    if arm == "quic":
        base = f"chroot {H3_ROOT} {H3_CURL} --http3-only"
    else:
        base = "curl --http2"

    # timeout wraps the whole invocation. Mininet's cmd() blocks until the
    # command returns, so without this a stalled transfer freezes the run.
    cmd = (f"timeout {TRANSFER_TIMEOUT_S} {base} -k -s -o /dev/null "
           f"-w '{CURL_FMT}\\n' '{url}'")

    out = client.cmd(cmd)

    rows = []
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) == 4 and parts[0] == "200":
            rows.append([float(x) for x in parts[1:]])
    if len(rows) != count:
        return None

    return {
        "ttfb_ms": round(rows[0][0] * 1000, 3),
        "total_ms": round(sum(r[1] for r in rows) * 1000, 3),
        "bytes": int(sum(r[2] for r in rows)),
        "objects": len(rows),
    }


def set_cc(arm):
    if arm == "tcp-cubic":
        sh("sysctl -w net.ipv4.tcp_congestion_control=cubic")
    elif arm == "tcp-bbr":
        sh("sysctl -w net.ipv4.tcp_congestion_control=bbr")
    # For QUIC the kernel setting is irrelevant. QUIC carries its own
    # congestion control in user space, which is precisely the constraint
    # that forces the bracketing design rather than a factorial one.


# =============================================================================
# Matrix
# =============================================================================

def matrix(mode, only_profile=None, only_workload=None):
    out = []
    if mode == "smoke":
        for arm in ARMS:
            out.append(dict(block="smoke", profile="P4", arm=arm,
                            workload="W3", bw=DEFAULT_BW, loss=0))
        return out

    profiles = [only_profile] if only_profile else list(PROFILES)
    loads = [only_workload] if only_workload else list(WORKLOADS)

    for p in profiles:
        for arm in ARMS:
            for w in loads:
                out.append(dict(block="primary", profile=p, arm=arm,
                                workload=w, bw=DEFAULT_BW, loss=0))
    if only_profile or only_workload:
        return out

    for loss in (0.5, 1, 2, 5):
        for arm in ARMS:
            for w in WORKLOADS:
                out.append(dict(block="loss", profile="P3", arm=arm,
                                workload=w, bw=DEFAULT_BW, loss=loss))
    for bw in BW_SWEEP:
        if bw == DEFAULT_BW:
            continue
        for p in PROFILES:
            for arm in ARMS:
                out.append(dict(block="bandwidth", profile=p, arm=arm,
                                workload="W1", bw=bw, loss=0))
    return out


# =============================================================================
# Execution
# =============================================================================

def execute(conditions, reps, outfile):
    groups = {}
    for c in conditions:
        groups.setdefault((c["profile"], c["bw"], c["loss"]), []).append(c)

    print(f"\n{len(conditions)} conditions x {reps} reps "
          f"= {len(conditions)*reps} transfers")
    print(f"{len(groups)} network configurations to build\n")

    rows = []
    t0 = time.time()

    for gi, (key, group) in enumerate(groups.items(), 1):
        profile, bw, loss = key
        print(f"[{gi}/{len(groups)}] {profile}, {bw} Mbit/s, loss {loss}%")

        net, client, server = build_net(profile, bw,
                                        loss if loss > 0 else None,
                                        burst=(loss > 0))
        try:
            v = verify_path(client, PROFILES[profile]["rtt_ms"])
            if v:
                print(f"    path {v[0]:.1f} ms, deviation {v[1]:.1f}%")
                if v[1] > 5:
                    print("    WARNING: host is busy, timings will be noisy")

            # Capacity only matters for workloads that move real data.
            if any(c["workload"] in ("W1", "W2") for c in group):
                got, ratio = verify_capacity(client, server, bw)
                if got is None:
                    print("    capacity check unavailable (install iperf)")
                elif ratio < MIN_ACCEPTABLE_BW_RATIO:
                    print(f"    capacity {got:.1f} of {bw} Mbit/s "
                          f"({ratio*100:.0f}%)")
                    print("    WARNING: the host still cannot deliver this "
                          "bottleneck.")
                    print("    Lower DEFAULT_BW further, or drop the bulk "
                          "workloads and keep W3 only.")
                else:
                    print(f"    capacity {got:.1f} of {bw} Mbit/s "
                          f"({ratio*100:.0f}%)")

            if not start_server(server):
                print("    server failed to start, skipping this group")
                continue

            # Warm-up per arm, discarded. The first QUIC connection to a
            # freshly started server pays one-off initialisation.
            for arm in ARMS:
                set_cc(arm)
                transfer(client, arm, "W3")

            schedule = [(c, r) for c in group for r in range(reps)]
            random.shuffle(schedule)

            ok = bad = 0
            for i, (c, rep) in enumerate(schedule):
                set_cc(c["arm"])
                m = transfer(client, c["arm"], c["workload"])
                if m is None:
                    bad += 1
                else:
                    ok += 1
                    rows.append({**{k: c[k] for k in
                                    ("block", "profile", "arm", "workload",
                                     "bw", "loss")}, "rep": rep, **m})
                # A brief pause lets sockets leave TIME_WAIT. Without it,
                # hundreds of consecutive connections exhaust the ephemeral
                # port range and later transfers fail for reasons that have
                # nothing to do with the protocols under test.
                if c["workload"] in ("W1", "W2"):
                    time.sleep(0.3)

                if (i + 1) % 5 == 0:
                    rate = (time.time() - t0) / (i + 1)
                    left = rate * (len(schedule) - i - 1) / 60
                    print(f"    {i+1}/{len(schedule)}  "
                          f"~{left:.0f} min remaining    ", end="\r")

            print(f"    {ok} ok, {bad} failed, "
                  f"{(time.time()-t0)/60:.1f} min elapsed")
        finally:
            server.cmd("pkill -f 'caddy run'")
            net.stop()
            sh("mn -c")

        if rows:
            with open(outfile, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)

    return rows


# =============================================================================
# Reporting
# =============================================================================

def floor_of(rows, profile, workload, arm):
    v = sorted(r["ttfb_ms"] for r in rows
               if r["profile"] == profile and r["workload"] == workload
               and r["arm"] == arm)
    if not v:
        return None, 0, 0
    clean = [x for x in v if x <= v[0] * CLEAN_MARGIN]
    return v[0], len(clean), len(v)


def report(rows):
    if not rows:
        print("\nNo successful transfers. Send the output above.")
        return

    keys = sorted({(r["profile"], r["workload"]) for r in rows})

    print("\n" + "=" * 76)
    print("Minimum time to first byte, ms   [clean runs / total]")
    print("=" * 76)
    line = f"\n  {'prof':<6}{'load':<6}"
    for a in ARMS:
        line += f"{a:>22}"
    print(line)
    print("  " + "-" * 72)

    for p, w in keys:
        line = f"  {p:<6}{w:<6}"
        for a in ARMS:
            f_, c, n = floor_of(rows, p, w, a)
            line += f"{f_:>14.1f} [{c}/{n}]" if f_ else f"{'-':>22}"
        print(line)

    print("\n" + "=" * 76)
    print("QUIC against the TCP bracket, ms   positive means QUIC is faster")
    print("=" * 76)
    print(f"\n  {'prof':<6}{'load':<6}{'RTT':>6}{'vs CUBIC':>12}"
          f"{'vs BBR':>12}{'verdict':>24}")
    print("  " + "-" * 72)

    for p, w in keys:
        q, _, _ = floor_of(rows, p, w, "quic")
        c, _, _ = floor_of(rows, p, w, "tcp-cubic")
        b, _, _ = floor_of(rows, p, w, "tcp-bbr")
        if None in (q, c, b):
            continue
        dc, db = c - q, b - q
        v = ("leads both" if dc > 0 and db > 0 else
             "trails both" if dc < 0 and db < 0 else "inside bracket")
        rtt = PROFILES[p]["rtt_ms"]
        print(f"  {p:<6}{w:<6}{rtt:>6}{dc:>+12.1f}{db:>+12.1f}{v:>24}")

    print("""
  How to read this

  The bracket exists because QUIC's congestion control cannot be selected,
  so any single comparison could be dismissed as a favourable pairing.
  Leading both bounds removes that objection.

  Then read down the RTT column. Prediction H1 says the advantage should
  grow with round trip time. Pilot measurement at 186 ms gave a saving of
  0.98 round trips against a theoretical 1.00. If the same ratio holds at
  63 ms, the mechanism is confirmed across the range. If it does not, that
  is the more interesting result and worth pursuing.
""")


# =============================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--profile", choices=list(PROFILES))
    ap.add_argument("--workload", choices=list(WORKLOADS),
                    help="run one workload only. W3 is quick, W2 is slow.")
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--out", default="results.csv")
    a = ap.parse_args()

    if not (a.smoke or a.all or a.profile or a.workload):
        ap.print_help()
        print("""
Suggested order

  sudo python3 RUN.py --smoke
      three conditions, about a minute, confirms the pipeline

  sudo python3 RUN.py --workload W3
      all four profiles, handshake workload only, about ten minutes.
      This is the workload that carries prediction H1.

  sudo python3 RUN.py --workload W1
      all four profiles, bulk transfer, 15 repetitions, about ten minutes.
      Watch the capacity line. If the host cannot deliver the configured
      bottleneck, these numbers describe the machine and not the network.

  sudo python3 RUN.py --workload W2 --reps 10
      the multiplexing workload. Each transfer fetches a hundred objects,
      so this is the slow one.

  sudo python3 RUN.py --all
      everything. Run it overnight.
""")
        return

    preflight()

    if a.smoke:
        rows = execute(matrix("smoke"), a.reps or REPS_SMOKE,
                       "results_smoke.csv")
    elif a.profile or a.workload:
        tag = (a.profile or "") + ("_" + a.workload if a.workload else "")
        # Bulk workloads move a thousand times more data per transfer, so
        # they get fewer repetitions by default. The minimum stabilises
        # quickly and the full distribution is not needed.
        default_reps = 15 if a.workload in ("W1", "W2") else REPS_FULL
        rows = execute(matrix("full", a.profile, a.workload),
                       a.reps or default_reps, f"results{tag}.csv")
    else:
        rows = execute(matrix("full"), a.reps or REPS_FULL, a.out)

    report(rows)


if __name__ == "__main__":
    main()
