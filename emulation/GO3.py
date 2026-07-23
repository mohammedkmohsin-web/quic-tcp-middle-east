#!/usr/bin/env python3
"""
===============================================================================
 GO.py
 One script. Two typed commands. No long lines to get wrong.
===============================================================================

 Everything that was failing came from typing long commands by hand. A missing
 space between two arguments of chroot, a slash instead of a backslash, a
 lowercase P where an uppercase one was needed. None of it was a real problem
 with the research. All of it cost hours.

 So nothing is typed by hand any more. This script contains every command,
 runs them in the right order, and reports what happened.

 Use it like this:

     cp /media/sf_research/GO.py .
     sudo python3 GO.py

 That is all. Two lines.

 What it does
 ------------
   1. Clears anything left over from previous attempts
   2. Makes sure the test content and server configuration exist
   3. Makes sure the HTTP/3 client filesystem is in place
   4. Builds the emulated network at profile P4
   5. Starts the server inside the server host
   6. Runs one HTTP/2 transfer and one HTTP/3 transfer from the client host
   7. Prints both timings side by side

 If step 7 prints two numbers, the pipeline works and the experiments can
 start. If something fails, it says which step and why.
===============================================================================
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CADDY_CONFIG = "/etc/caddy/Caddyfile"
WEB_ROOT = "/var/www/quictest"
SERVER_IP = "10.0.0.2"
H3_ROOT = "/opt/h3root"
H3_CURL = "/usr/local/bin/curl"
CURL_IMAGE = "ymuski/curl-http3"

PROFILE = "P4"          # 186 ms, the worst measured regional path
REPEATS = 15            # more runs, because we keep only the clean ones
RTT_TARGET = 186


def sh(cmd, timeout=300):
    p = subprocess.run(cmd, shell=True, capture_output=True,
                       text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def say(msg):
    print(f"  {msg}")


def die(msg):
    print(f"\n  STOPPED: {msg}\n")
    sys.exit(1)


# =============================================================================

def step1_clean():
    print("\n[1] Clearing previous state")
    sh("systemctl stop caddy")
    sh("systemctl disable caddy")
    sh("pkill -f 'caddy run'")
    sh("mn -c")
    say("done")


def step1b_tune():
    """
    Raise the UDP socket buffers.

    Linux ships small default receive and send buffers for UDP. QUIC runs
    over UDP, so a handshake that would fit comfortably in a TCP socket can
    overflow here, producing drops and retransmissions that appear as extra
    round trips. The QUIC libraries warn about this explicitly.

    This is the first suspect for the handshake anomaly. It costs nothing to
    rule out, and leaving it unset would be a configuration error a reviewer
    could reasonably object to.
    """
    print("\n[1b] Tuning UDP buffers")
    for key, val in [("net.core.rmem_max", 7500000),
                     ("net.core.wmem_max", 7500000),
                     ("net.core.rmem_default", 2500000),
                     ("net.core.wmem_default", 2500000)]:
        rc, before = sh(f"sysctl -n {key}")
        sh(f"sysctl -w {key}={val}")
        rc, after = sh(f"sysctl -n {key}")
        say(f"{key}: {before.strip()} -> {after.strip()}")


def step2_content():
    print("\n[2] Test content and server configuration")

    if not os.path.exists(f"{WEB_ROOT}/tiny.bin"):
        say("creating content, this takes a minute")
        os.makedirs(f"{WEB_ROOT}/small", exist_ok=True)
        sh(f"dd if=/dev/urandom of={WEB_ROOT}/tiny.bin bs=1K count=10")
        sh(f"dd if=/dev/urandom of={WEB_ROOT}/large.bin bs=1M count=10")
        for i in range(100):
            sh(f"dd if=/dev/urandom of={WEB_ROOT}/small/obj{i:03d}.bin "
               f"bs=1K count=20")
    say("content present")

    config = f"""{{
    auto_https disable_redirects
    servers {{
        protocols h1 h2 h3
    }}
}}

https://{SERVER_IP} {{
    tls internal
    root * {WEB_ROOT}
    file_server
    header Cache-Control "no-store"
}}
"""
    os.makedirs("/etc/caddy", exist_ok=True)
    with open(CADDY_CONFIG, "w") as f:
        f.write(config)
    say("configuration written")


def step3_h3client():
    print("\n[3] HTTP/3 client")

    if os.path.exists(f"{H3_ROOT}{H3_CURL}"):
        rc, out = sh(f"chroot {H3_ROOT} {H3_CURL} --version")
        if "HTTP3" in out:
            say("already in place: " + out.splitlines()[0][:60])
            return
        say("present but not working, rebuilding")
        sh(f"rm -rf {H3_ROOT}")

    say("extracting the container filesystem")
    sh("systemctl start docker")
    time.sleep(2)

    rc, out = sh(f"docker image inspect {CURL_IMAGE}")
    if rc != 0:
        say("pulling the image, this may take a few minutes")
        rc, out = sh(f"docker pull {CURL_IMAGE}", timeout=900)
        if rc != 0:
            die("could not pull the container image. Check the network.")

    sh("docker rm -f h3fs")
    sh(f"docker create --name h3fs {CURL_IMAGE}")
    os.makedirs(H3_ROOT, exist_ok=True)
    rc, out = sh(f"docker export h3fs | tar -x -C {H3_ROOT}")
    sh("docker rm -f h3fs")

    rc, out = sh(f"chroot {H3_ROOT} {H3_CURL} --version")
    if "HTTP3" not in out:
        die("the extracted client does not report HTTP3:\n" + out[:300])
    say("working: " + out.splitlines()[0][:60])


def step4_run():
    print(f"\n[4] Building the network at profile {PROFILE}")

    try:
        from mininet.net import Mininet
        from mininet.node import OVSKernelSwitch
        from mininet.link import TCLink
        from mininet.log import setLogLevel
        from topology import PROFILES, buffer_packets
    except ImportError as e:
        die(f"missing dependency: {e}")

    setLogLevel("critical")
    profile = PROFILES[PROFILE]
    one_way = profile["rtt_ms"] / 2.0
    queue = buffer_packets(50, profile["rtt_ms"])

    say(f"{profile['name']}")
    say(f"round trip {profile['rtt_ms']} ms, queue {queue} packets")

    net = Mininet(switch=OVSKernelSwitch, link=TCLink, controller=None,
                  autoSetMacs=True)
    client = net.addHost("client", ip="10.0.0.1/24")
    server = net.addHost("server", ip=f"{SERVER_IP}/24")
    s1 = net.addSwitch("s1", failMode="standalone")
    s2 = net.addSwitch("s2", failMode="standalone")
    net.addLink(client, s1)
    net.addLink(server, s2)
    net.addLink(s1, s2, cls=TCLink, bw=50,
                delay=f"{one_way:.2f}ms",
                jitter=f"{profile['jitter_ms']:.2f}ms",
                max_queue_size=queue, use_htb=True)
    net.start()

    try:
        print("\n[5] Verifying the emulated path")
        out = client.cmd(f"ping -c 10 -q {SERVER_IP}")
        line = [l for l in out.splitlines() if l.startswith("rtt")]
        if not line:
            die("the client cannot reach the server")
        say(line[0])
        measured = float(line[0].split("=")[1].split("/")[0])
        drift = abs(measured - RTT_TARGET) / RTT_TARGET * 100
        say(f"target {RTT_TARGET} ms, measured {measured:.1f} ms, "
            f"deviation {drift:.1f}%")
        if drift > 5:
            say("WARNING: deviation above five percent. Close other "
                "programs on the host and try again.")

        print("\n[6] Starting the server inside the server host")
        server.cmd(f"XDG_DATA_HOME=/tmp/cd XDG_CONFIG_HOME=/tmp/cd "
                   f"/usr/bin/caddy run --config {CADDY_CONFIG} "
                   f"> /tmp/caddy.log 2>&1 &")
        for _ in range(20):
            time.sleep(1)
            if "443" in server.cmd("ss -lntu | grep 443 || true"):
                break
        else:
            log = ""
            if os.path.exists("/tmp/caddy.log"):
                log = open("/tmp/caddy.log").read()[-500:]
            die("the server did not start listening on 443\n" + log)
        say("listening on 443")

        print(f"\n[7] Measuring, {REPEATS} runs of each protocol")
        fmt = r'%{http_code} %{time_starttransfer} %{time_total}'

        # A warm run first, discarded. The very first QUIC connection to a
        # freshly started server pays one-off initialisation that has nothing
        # to do with the protocol, and including it would distort the result.
        client.cmd(f"chroot {H3_ROOT} {H3_CURL} -k -s -o /dev/null "
                   f"--http3-only https://{SERVER_IP}/tiny.bin")
        client.cmd(f"curl -k -s -o /dev/null --http2 "
                   f"https://{SERVER_IP}/tiny.bin")
        say("warm-up run discarded")

        h2_runs, h3_runs = [], []
        for i in range(REPEATS):
            r = parse(client.cmd(
                f"curl -k -s -o /dev/null -w '{fmt}' --http2 "
                f"https://{SERVER_IP}/tiny.bin"))
            if r:
                h2_runs.append(r)
            r = parse(client.cmd(
                f"chroot {H3_ROOT} {H3_CURL} -k -s -o /dev/null -w '{fmt}' "
                f"--http3-only https://{SERVER_IP}/tiny.bin"))
            if r:
                h3_runs.append(r)
            print(f"        run {i+1}/{REPEATS}", end="\r")
        print(" " * 30, end="\r")

        report_many(h2_runs, h3_runs)

        server.cmd("pkill -f 'caddy run'")

    finally:
        net.stop()
        sh("mn -c")


def parse(out):
    parts = out.strip().split()
    if len(parts) < 3 or parts[0] != "200":
        return None
    return float(parts[1]) * 1000, float(parts[2]) * 1000


def robust(values):
    """
    Summarise a set of timing samples.

    Timing noise on a virtual machine only ever adds. A scheduler delay makes
    a transfer look slower; nothing makes it look faster than the network
    allows. The minimum is therefore the sample least contaminated by the
    host, and it is the figure that best represents the path.

    The median is the wrong statistic here. Across three sessions the minimum
    for QUIC held steady between 389 and 409 ms while the median wandered
    from 442 to 931 ms. That is not instability in the protocol. It is a
    bimodal distribution: clean runs near the floor, interrupted runs far
    above it, and a median that lands on whichever cluster happened to be
    larger.

    We report the minimum as the primary figure, and count how many runs sit
    within 15 percent of it. That count is the honest measure of how clean
    the measurement session was.
    """
    if not values:
        return None
    v = sorted(values)
    floor = v[0]
    clean = [x for x in v if x <= floor * 1.15]
    return {
        "min": floor,
        "clean_n": len(clean),
        "total_n": len(v),
        "clean_mean": sum(clean) / len(clean),
        "max": v[-1],
    }


def report_many(h2_runs, h3_runs):
    print()
    print("  " + "=" * 64)
    print(f"  {'protocol':<14}{'minimum':>12}{'clean runs':>14}"
          f"{'clean mean':>14}{'worst':>10}")
    print("  " + "-" * 64)

    def line(name, runs):
        st = robust([r[0] for r in runs])
        if st is None:
            print(f"  {name:<14}   all runs failed")
            return None
        print(f"  {name:<14}{st['min']:>9.1f} ms"
              f"{st['clean_n']:>8} of {st['total_n']:<3}"
              f"{st['clean_mean']:>11.1f} ms{st['max']:>7.0f} ms")
        return st

    s2 = line("HTTP/2 TCP", h2_runs)
    s3 = line("HTTP/3 QUIC", h3_runs)
    print("  " + "=" * 64)

    if s2 is None or s3 is None:
        print("\n  Send this output and the failing side can be fixed.\n")
        return

    m2, m3 = s2["min"], s3["min"]
    r2, r3 = m2 / RTT_TARGET, m3 / RTT_TARGET

    print(f"""
  Round trips consumed, on a {RTT_TARGET} ms path

    HTTP/2 over TCP    theory 3.00    measured {r2:.2f}
                       TCP handshake, TLS 1.3, then the request

    HTTP/3 over QUIC   theory 2.00    measured {r3:.2f}
                       combined handshake, then the request
""")

    quality = min(s2["clean_n"], s3["clean_n"]) / max(s2["total_n"], 1)
    if quality < 0.4:
        print(f"""  Measurement quality is poor. Only {quality*100:.0f} percent of runs sat
  near the floor, so the host was busy for most of the session. The
  minima above are still usable, but close everything else on the host
  and run again before trusting anything finer.
""")

    if m3 < m2:
        saved = m2 - m3
        print(f"""  QUIC saves {saved:.0f} ms, which is {saved/RTT_TARGET:.2f} round trips.

  That is prediction H1 behaving as the design implies. QUIC merges the
  transport and cryptographic handshakes, so the expected saving is one
  full round trip, and one round trip on this path is {RTT_TARGET} ms.

  Next:  sudo python3 run_experiments.py --smoke
""")
    else:
        print(f"""  QUIC is behind by {m3-m2:.0f} ms even at its floor, using {r3-2:.1f} more
  round trips than its design requires. Since this is the minimum rather
  than the median, host noise is not the explanation.

  Check, in order: UDP buffer sizes, certificate chain length against
  QUIC's anti-amplification limit, and cross-implementation behaviour
  between the quiche client and the quic-go server.
""")


def report(h2_raw, h3_raw):
    h2, h3 = parse(h2_raw), parse(h3_raw)

    print()
    print("  " + "=" * 60)
    print("  protocol      time to first byte      total")
    print("  " + "-" * 60)

    if h2:
        print(f"  HTTP/2 TCP    {h2[0]:>15.1f} ms {h2[1]:>12.1f} ms")
    else:
        print(f"  HTTP/2 TCP    FAILED: {h2_raw[:40]}")

    if h3:
        print(f"  HTTP/3 QUIC   {h3[0]:>15.1f} ms {h3[1]:>12.1f} ms")
    else:
        print(f"  HTTP/3 QUIC   FAILED: {h3_raw[:40]}")

    print("  " + "=" * 60)

    if h2 and h3:
        diff = h2[0] - h3[0]
        rtts = diff / RTT_TARGET
        print(f"""
  QUIC saves {diff:.1f} ms on connection setup, which is {rtts:.2f} round
  trips at this path length.

  This is the mechanism prediction H1 rests on. QUIC merges the transport
  and cryptographic handshakes, so it should save close to one full round
  trip against TCP with TLS 1.3. On a 186 ms path that saving is large in
  absolute terms, which is exactly why the regional case matters.

  A single measurement proves nothing on its own. But the pipeline now
  works end to end, and the experiment can run.

  Next:  sudo python3 run_experiments.py --smoke
""")
    else:
        print("""
  One or both transfers failed. Send this output and it can be fixed.
""")


def main():
    if os.geteuid() != 0:
        print("Run it with sudo:\n\n    sudo python3 GO.py\n")
        sys.exit(1)

    print("=" * 64)
    print("  Full pipeline test")
    print("=" * 64)

    step1_clean()
    step1b_tune()
    step2_content()
    step3_h3client()
    step4_run()


if __name__ == "__main__":
    main()
