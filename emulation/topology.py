#!/usr/bin/env python3
"""
===============================================================================
 topology.py
 Emulated dumbbell topology with measured network condition profiles
 Project: Performance of QUIC and TCP under Measured Middle Eastern Conditions
===============================================================================

 This builds the environment described in Section IV of the paper.

     client ---- s1 ==== bottleneck ==== s2 ---- server

 All impairment sits on the single link between the two switches, so every
 packet in both directions crosses exactly one point of control.

 The profiles come from Table III and are not invented. Each round trip time
 is split in half across the two directions, because netem delays packets on
 egress only.

 Requirements
 ------------
     sudo apt update
     sudo apt install -y mininet openvswitch-switch
     sudo python3 topology.py --profile P1

 It must run as root. Mininet manipulates kernel networking directly.

 Useful once inside the Mininet prompt:
     client ping -c 10 server        check the round trip time
     client curl -sI https://10.0.0.2/    check the server answers
     exit                            tear the network down cleanly
===============================================================================
"""

import argparse
import sys

try:
    from mininet.net import Mininet
    from mininet.node import OVSKernelSwitch
    from mininet.link import TCLink
    from mininet.cli import CLI
    from mininet.log import setLogLevel, info
except ImportError:
    print("Mininet is not installed. On Ubuntu:")
    print("    sudo apt install -y mininet openvswitch-switch")
    sys.exit(1)


# =============================================================================
# Measured profiles, from Table III of the paper
#
#   rtt_ms     total round trip time
#   jitter_ms  interquartile range of round trip time, from Section III-F
#   loss_pct   measured loss. Section III-F found none, so these are near zero
#              and any non zero value passed on the command line belongs to the
#              declared sensitivity sweep, not to the measured conditions.
# =============================================================================

PROFILES = {
    "P1": {"name": "Regional direct (Turkey)",     "rtt_ms": 63,  "jitter_ms": 0.22, "loss_pct": 0.10},
    "P2": {"name": "European control (Germany)",   "rtt_ms": 68,  "jitter_ms": 0.20, "loss_pct": 0.00},
    "P3": {"name": "Regional detoured (Saudi)",    "rtt_ms": 145, "jitter_ms": 0.23, "loss_pct": 0.10},
    "P4": {"name": "Regional worst case (UAE)",    "rtt_ms": 186, "jitter_ms": 0.20, "loss_pct": 0.00},
}

DEFAULT_BW_MBIT = 50
MTU_BYTES = 1500


def buffer_packets(bw_mbit, rtt_ms):
    """
    Queue depth of one bandwidth delay product, expressed in packets.

    This choice is deliberate and must be stated in the paper. A deeper buffer
    favours loss based control such as CUBIC, because it can keep filling the
    queue before it sees a drop. A shallower buffer favours rate based control
    such as BBR. Any value would bias the comparison, so what matters is that
    the value is principled and declared.
    """
    bytes_in_flight = (bw_mbit * 1e6 / 8.0) * (rtt_ms / 1000.0)
    return max(int(bytes_in_flight / MTU_BYTES), 10)


def build(profile_key, bw_mbit, loss_override, use_burst_loss):
    profile = PROFILES[profile_key]
    rtt = profile["rtt_ms"]
    one_way = rtt / 2.0
    loss = profile["loss_pct"] if loss_override is None else loss_override
    queue = buffer_packets(bw_mbit, rtt)

    info("\n*** Profile %s: %s\n" % (profile_key, profile["name"]))
    info("*** round trip %.0f ms, %.1f ms each way\n" % (rtt, one_way))
    info("*** bottleneck %d Mbit/s, queue %d packets\n" % (bw_mbit, queue))
    info("*** loss %.2f%%%s\n" % (loss, "  (sensitivity sweep)"
                                  if loss_override is not None else ""))

    net = Mininet(switch=OVSKernelSwitch, link=TCLink, controller=None,
                  autoSetMacs=True)

    client = net.addHost("client", ip="10.0.0.1/24")
    server = net.addHost("server", ip="10.0.0.2/24")
    s1 = net.addSwitch("s1", failMode="standalone")
    s2 = net.addSwitch("s2", failMode="standalone")

    # Access links are deliberately clean. Everything we want to study
    # happens on the middle link.
    net.addLink(client, s1)
    net.addLink(server, s2)

    # The bottleneck. Delay is halved because it is applied on each side.
    link_args = {
        "bw": bw_mbit,
        "delay": "%.2fms" % one_way,
        "jitter": "%.2fms" % profile["jitter_ms"],
        "max_queue_size": queue,
        "use_htb": True,
    }
    if loss > 0:
        link_args["loss"] = loss

    net.addLink(s1, s2, cls=TCLink, **link_args)

    net.start()

    if use_burst_loss and loss > 0:
        apply_burst_loss(net, loss)

    return net


def apply_burst_loss(net, loss_pct):
    """
    Replace independent loss with the Gilbert-Elliott model.

    Independent loss is the convention in the literature and it is wrong.
    Real networks drop packets in bursts, produced by congestion episodes and
    handovers. At the same average rate, bursty loss damages a transport more
    than scattered loss, because recovery mechanisms are defeated by
    consecutive drops rather than isolated ones.

    netem's gemodel takes p and r, the transition probabilities between the
    good and bad states. The stationary loss rate is p / (p + r). Fixing the
    mean burst length at five packets gives r = 0.2, and p follows.
    """
    r = 0.2
    f = loss_pct / 100.0
    p = (f * r) / (1.0 - f) if f < 1.0 else r

    info("*** replacing independent loss with Gilbert-Elliott\n")
    info("***   p=%.4f  r=%.2f  mean burst %.0f packets\n" % (p, r, 1.0 / r))

    for switch in (net.get("s1"), net.get("s2")):
        for intf in switch.intfList():
            if intf.link and "s1" in str(intf.link) and "s2" in str(intf.link):
                switch.cmd("tc qdisc change dev %s root netem "
                           "loss gemodel %.4f%% %.2f%%" % (intf.name, p * 100, r * 100))


def verify(net):
    """
    Confirm the emulator reproduces its target before any experiment runs.

    An emulator that misses its target is worse than none at all, because the
    numbers it produces still look measured.
    """
    info("\n*** verification\n")
    client, server = net.get("client"), net.get("server")

    out = client.cmd("ping -c 20 -i 0.2 -q %s" % server.IP())
    for line in out.splitlines():
        if "rtt" in line or "packet loss" in line:
            info("***   %s\n" % line.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=list(PROFILES), default="P1")
    ap.add_argument("--bw", type=int, default=DEFAULT_BW_MBIT,
                    help="bottleneck in Mbit/s (10, 50 or 100)")
    ap.add_argument("--loss", type=float, default=None,
                    help="override loss %% for the sensitivity sweep")
    ap.add_argument("--burst", action="store_true",
                    help="use Gilbert-Elliott loss instead of independent")
    ap.add_argument("--no-cli", action="store_true",
                    help="verify and exit, for scripted runs")
    args = ap.parse_args()

    setLogLevel("info")
    net = build(args.profile, args.bw, args.loss, args.burst)

    try:
        verify(net)
        if not args.no_cli:
            info("\n*** Mininet ready. Type exit to tear it down.\n\n")
            CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
