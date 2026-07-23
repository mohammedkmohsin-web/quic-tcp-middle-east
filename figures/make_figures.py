#!/usr/bin/env python3
"""
===============================================================================
 make_figures.py
 The three figures the paper needs, drawn from your own measurements
 Project: Performance of QUIC and TCP under Measured Middle Eastern Conditions
===============================================================================

 The journal will not accept images taken from the web. Every figure must be
 your own work. These are drawn directly from the CSV files you collected, so
 they cannot drift out of step with the numbers in the tables.

 Fig. 1  Emulator topology
         Referenced in Section IV-B, currently an empty placeholder. A paper
         that points at a figure which does not exist is an easy thing for a
         reviewer to catch.

 Fig. 2  Distance against minimum round trip time, by source country
         The measurement result, in the form a table cannot convey. The
         physical minimum appears as a dashed line, and the regional
         neighbours sit far above it while North America sits close to it.

 Fig. 3  Handshake saving against path latency
         Four measured points against the one-round-trip prediction. This
         figure replaces Table VII, so it costs no net space.

 Requirements
     pip3 install matplotlib

 Run it in the directory holding your CSV files:
     python3 make_figures.py

 Output: fig1_topology.pdf, fig2_inflation.pdf, fig3_saving.pdf
 Vector PDF, greyscale, sized for a single IEEE column.
===============================================================================
"""

import csv
import os
import sys
from collections import defaultdict

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle, FancyArrowPatch
except ImportError:
    print("matplotlib is missing:  pip3 install matplotlib")
    sys.exit(1)


# IEEE single column is 3.5 inches wide. Figures are drawn at that width so
# they need no scaling, which is what keeps the fonts legible after import.
COL_W = 3.45
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.titlesize": 8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "legend.fontsize": 7,
    "axes.linewidth": 0.6,
    "grid.linewidth": 0.4,
    "lines.linewidth": 1.0,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
})

RTT = {"P1": 63, "P2": 68, "P3": 145, "P4": 186}


def read(path):
    if not os.path.exists(path):
        print(f"  missing: {path}")
        return None
    return list(csv.DictReader(open(path)))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# =============================================================================
# Fig. 1 -- emulator topology
# =============================================================================

def fig1():
    print("Fig. 1  emulator topology")
    fig, ax = plt.subplots(figsize=(COL_W, 1.65))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3.45)
    ax.axis("off")

    def box(x, label, sub=None, w=1.5, h=0.85, fill="white"):
        ax.add_patch(Rectangle((x, 1.1), w, h, fill=True, facecolor=fill,
                               edgecolor="black", linewidth=0.8))
        ax.text(x + w / 2, 1.1 + h / 2 + (0.1 if sub else 0), label,
                ha="center", va="center", fontsize=7.5)
        if sub:
            ax.text(x + w / 2, 1.1 + h / 2 - 0.17, sub,
                    ha="center", va="center", fontsize=6, style="italic")

    box(0.2, "client", "curl", fill="#eaf2fb")
    box(2.4, "s1", fill="#f5f5f5")
    box(6.1, "s2", fill="#f5f5f5")
    box(8.3, "server", "Caddy", fill="#eaf2fb")

    # plain links
    for x0, x1 in [(1.7, 2.4), (7.6, 8.3)]:
        ax.plot([x0, x1], [1.52, 1.52], color="black", linewidth=0.8)

    # the impaired link
    ax.plot([3.9, 6.1], [1.52, 1.52], color="#c0392b", linewidth=1.8)
    # The box is sized around the wider of the two text lines, with margin.
    ax.add_patch(Rectangle((3.30, 2.28), 3.40, 0.88, fill=True,
                           facecolor="#fdeae7", edgecolor="#c0392b",
                           linewidth=0.8))
    ax.text(5.0, 2.93, "netem", ha="center", va="center", fontsize=6.5,
            color="#c0392b")
    ax.text(5.0, 2.55, "delay, jitter, rate", ha="center", va="center",
            fontsize=5.6, color="#8c2a1e")
    ax.plot([5.0, 5.0], [1.60, 2.26], color="#c0392b", linewidth=0.6,
            linestyle=":")

    ax.text(5.0, 0.70, "bottleneck link", ha="center", fontsize=6.5,
            style="italic", color="#c0392b")
    ax.text(0.2, 0.18,
            "Both endpoints run inside the emulated hosts, so every packet "
            "crosses one point of control.",
            fontsize=5.6, color="0.25")

    fig.savefig("fig1_topology.pdf")
    plt.close(fig)
    print("  wrote fig1_topology.pdf")


# =============================================================================
# Fig. 2 -- distance against latency
# =============================================================================

def fig2():
    print("Fig. 2  distance against minimum round trip time")
    rows = read("anchor_rtt_stats.csv")
    if not rows:
        return

    # Aggregate by country, keeping only countries with enough probes for the
    # median to mean anything. Probes reporting heavy loss are excluded as
    # faulty, following Section III-D.
    by = defaultdict(list)
    for r in rows:
        loss = num(r.get("loss_pct"))
        d, t = num(r.get("distance_km")), num(r.get("rtt_min"))
        if None in (d, t) or d < 50:
            continue
        if loss is not None and loss > 20:
            continue
        by[r["country"]].append((d, t))

    def med(v):
        s = sorted(v)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    pts = {cc: (med([x[0] for x in v]), med([x[1] for x in v]), len(v))
           for cc, v in by.items() if len(v) >= 3}

    neighbours = {"IR", "AE", "SA", "TR", "KW", "JO", "SY"}

    # Label offsets in points, tuned per country. The region below 4000 km is
    # dense, so labels there are pushed clear of the cluster and carry a white
    # background so they stay readable where they cross a marker.
    LABELS = {
        "IR": (-8, 9), "SA": (7, 5), "AE": (7, 5), "TR": (7, -11),
        "DE": (7, -11), "US": (8, 2), "IN": (8, 2),
        "AU": (-4, -13), "JP": (-13, -12),
    }

    fig, ax = plt.subplots(figsize=(COL_W, 2.6))

    # the physical floor: light in fibre, there and back
    xs = [0, 15000]
    ax.plot(xs, [2 * x / (299.792 * 2 / 3) for x in xs],
            color="#2c6fbb", linestyle="--", linewidth=0.9)

    for cc, (d, t, n) in pts.items():
        near = cc in neighbours
        ax.scatter(d, t, s=42 if near else 17,
                   marker="^" if near else "o",
                   facecolor="#c0392b" if near else "#9dbdd8",
                   edgecolor="#7a1f14" if near else "#4a6d87",
                   linewidth=0.5, zorder=4 if near else 3)
        if cc in LABELS:
            ax.annotate(cc, (d, t), textcoords="offset points",
                        xytext=LABELS[cc], fontsize=6.5, zorder=6,
                        color="#7a1f14" if near else "black",
                        fontweight="bold" if near else "normal",
                        bbox=dict(boxstyle="square,pad=0.12", fc="white",
                                  ec="none", alpha=0.82))

    ax.set_xlabel("Great-circle distance to the anchor (km)")
    ax.set_ylabel("Minimum round trip time (ms)")
    ax.set_xlim(0, 14500)
    ax.set_ylim(0, 375)
    ax.grid(True, color="0.85", linestyle="-", linewidth=0.4)
    ax.set_axisbelow(True)

    from matplotlib.lines import Line2D
    ax.legend(handles=[
        Line2D([], [], marker="^", color="none",
               markerfacecolor="#c0392b", markeredgecolor="#7a1f14",
               markersize=5.5, label="regional neighbour"),
        Line2D([], [], marker="o", color="none",
               markerfacecolor="#9dbdd8", markeredgecolor="#4a6d87",
               markersize=4, label="other"),
        Line2D([], [], color="#2c6fbb", linestyle="--", linewidth=0.9,
               label="physical minimum"),
    ], loc="lower right", frameon=False, handletextpad=0.4,
        borderaxespad=0.3)

    fig.savefig("fig2_inflation.pdf")
    plt.close(fig)
    print(f"  {len(pts)} countries plotted")
    print("  wrote fig2_inflation.pdf")


# =============================================================================
# Fig. 3 -- handshake saving against path latency
# =============================================================================

def fig3():
    print("Fig. 3  handshake saving against path latency")
    w3 = read("results_W3.csv")
    p2 = read("resultsP2_W3.csv")
    if not w3:
        return

    def floor(rows, prof, arm):
        v = sorted(num(r["ttfb_ms"]) for r in rows
                   if r["profile"] == prof and r["arm"] == arm)
        return v[0] if v else None

    xs, cubic, bbr = [], [], []
    for prof in ["P1", "P2", "P3", "P4"]:
        # P2 was re-measured under quiet conditions; that run is the one used
        src = p2 if (prof == "P2" and p2) else w3
        q = floor(src, prof, "quic")
        c = floor(src, prof, "tcp-cubic")
        b = floor(src, prof, "tcp-bbr")
        if None in (q, c, b):
            continue
        xs.append(RTT[prof])
        cubic.append(c - q)
        bbr.append(b - q)
        print(f"  {prof}: RTT {RTT[prof]} ms, saving {c-q:.1f} / {b-q:.1f} ms")

    fig, ax = plt.subplots(figsize=(COL_W, 2.4))

    # the prediction: exactly one round trip
    lim = [0, 205]
    ax.plot(lim, lim, color="#2c6fbb", linestyle="--", linewidth=1.0,
            label="predicted: one round trip")

    ax.scatter(xs, cubic, s=40, marker="o", facecolor="#c0392b",
               edgecolor="#7a1f14", linewidth=0.6, zorder=3,
               label="measured vs TCP CUBIC")
    ax.scatter(xs, bbr, s=40, marker="s", facecolor="#f0a500",
               edgecolor="#8a5f00", linewidth=0.6, zorder=3,
               label="measured vs TCP BBR")

    for x, y in zip(xs, cubic):
        ax.annotate(f"P{[63, 68, 145, 186].index(x)+1}", (x, y),
                    textcoords="offset points", xytext=(6, -9),
                    fontsize=6.5, fontweight="bold")

    ax.set_xlabel("Path round trip time (ms)")
    ax.set_ylabel("Handshake saving (ms)")
    ax.set_xlim(40, 205)
    ax.set_ylim(40, 205)
    ax.grid(True, color="0.85", linestyle="-", linewidth=0.4)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", frameon=False, handletextpad=0.4,
              borderaxespad=0.3)

    fig.savefig("fig3_saving.pdf")
    plt.close(fig)
    print("  wrote fig3_saving.pdf")


# =============================================================================

def main():
    print("Building figures from your measurement files\n")
    fig1()
    print()
    fig2()
    print()
    fig3()

    print("""
Captions to use in the paper

  Fig. 1.  Emulator topology. Both endpoints run inside emulated hosts, so
  every packet crosses the single impaired link exactly once.

  Fig. 2.  Minimum round trip time to the Erbil anchor against great-circle
  distance, by source country. The dashed line is the physical minimum for
  light in fibre over twice the distance. Regional neighbours lie far above
  it; North American sources lie close to it despite being six times more
  distant.

  Fig. 3.  Handshake saving of QUIC over TCP against path round trip time.
  The dashed line is the predicted saving of exactly one round trip. Points
  fall on it across a threefold range of latency, which is what makes the
  result a statement about mechanism rather than about one measurement.

Placement

  Fig. 1 goes in Section IV-B, where the text already refers to it.
  Fig. 2 goes in Section III-D, beside the path inflation discussion.
  Fig. 3 goes in Section VI-B, and replaces Table VII.
""")


if __name__ == "__main__":
    main()
