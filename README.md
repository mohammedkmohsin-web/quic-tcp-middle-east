# QUIC and TCP under Measured Middle Eastern Network Conditions

Measurement data, analysis code and emulation scripts for the paper
*Performance of QUIC and TCP under Measured Middle Eastern Network
Conditions: An Emulation Study Driven by Real Path Measurements*.

The study has two halves. The first measures how Middle Eastern network
paths actually behave, using the RIPE Atlas anchoring mesh. The second
reproduces those measured conditions in an emulator and compares HTTP/3
over QUIC against HTTP/2 over TCP running CUBIC and BBR.

Everything here is what produced the numbers in the paper. The figures are
generated from the same CSV files that the tables are drawn from, so the
two cannot drift apart.

---

## Repository layout

```
├── measurement/          RIPE Atlas collection and analysis
│   ├── step1_probes.py       probe inventory for a country
│   ├── step2_anchor.py       RTT statistics and path inflation
│   ├── step3_traceroute.py   AS-level paths, hop by hop
│   └── step4_diurnal.py      seven-day stability check
├── emulation/            Mininet and experiment control
│   ├── topology.py           dumbbell topology, four measured profiles
│   ├── GO3.py                end-to-end pipeline check
│   └── RUN4.py               experiment runner
├── figures/
│   └── make_figures.py       the three figures, drawn from data/
├── data/                 every result reported in the paper
└── README.md
```

---

## Measurement data

| File | Contents |
|---|---|
| `probes_IQ.csv` | The ten connected Iraqi probes, with ASN and location |
| `anchors_IQ.csv` | The Iraqi anchor record |
| `anchor_measurements.csv` | Live measurements targeting the anchor |
| `anchor_rtt_stats.csv` | **997 source probes**: RTT, jitter, loss, distance, path inflation |
| `as_paths.csv` | AS-level paths from selected sources to the anchor |
| `hop_details.csv` | Per-hop detail behind those paths |
| `diurnal_hourly.csv` | Seven days by hour of day, four countries |
| `decix_istanbul_members.csv` | Exchange membership, retrieved from PeeringDB |

`anchor_rtt_stats.csv` is the file behind Table II and Fig. 1. The
`inflation` column is the minimum observed round trip time divided by the
physical lower bound for that distance, so a value near 1 means the path is
close to a straight line.

## Emulation results

| File | Contents |
|---|---|
| `results_W3.csv` | **600 transfers**: four profiles, three transport arms, fifty repetitions |
| `resultsP2_W3.csv` | The P2 group, re-measured under quiet host conditions |
| `results_W1.csv` | The bulk transfer attempt, reported in Section VII-B as a failure |

`resultsP2_W3.csv` is the group reported in the paper. The original P2
measurement is not included because the emulated path deviated 17.3 percent
from target, indicating host contention. The discard is stated in the paper
rather than hidden.

`results_W1.csv` is incomplete by design. It is published because the
reason it failed, an emulation host that could not sustain the configured
bottleneck, is worth knowing for anyone attempting similar work.

---

## Requirements

Measurement needs only Python and network access:

```bash
pip3 install requests matplotlib
```

Emulation needs Linux. The experiments in the paper ran on Ubuntu 22.04:

```bash
sudo apt install -y mininet openvswitch-switch caddy docker.io iperf
sudo modprobe tcp_bbr
sudo docker pull ymuski/curl-http3
```

---

## Reproducing the measurement

No API key and no measurement credits are required. All of this reads
public data.

```bash
python3 measurement/step1_probes.py       # probe inventory
python3 measurement/step2_anchor.py       # RTT and path inflation
python3 measurement/step3_traceroute.py   # AS-level paths
python3 measurement/step4_diurnal.py      # seven-day stability
```

The probe population changes over time, so figures will differ from those
in the paper, which were retrieved on 22 July 2026.

## Reproducing the emulation

```bash
sudo python3 emulation/GO3.py                     # verify the pipeline
sudo python3 emulation/RUN4.py --smoke            # three conditions
sudo python3 emulation/RUN4.py --workload W3      # the full experiment
```

`GO3.py` should be run first. It checks that both endpoints can reach each
other inside the emulated hosts, and reports how closely the emulator
reproduces its target latency.

Two configuration details matter more than they appear to:

**UDP socket buffers.** The Linux default is far too small for QUIC.
Raising the receive and send buffers to 7.5 MB removed roughly two round
trips from every QUIC handshake in our pilot runs, turning a result in
which QUIC trailed TCP into one in which it led. `RUN4.py` raises them
automatically, but anyone reproducing this by hand should not skip it.

**Host resources.** Emulated timing degrades sharply when the host is
busy. With four cores allocated we measured a standard deviation of 0.54 ms
against a 186 ms target; with fewer, the same target gave 25.5 ms, which is
the same order as the protocol differences the study sets out to detect.

## Regenerating the figures

```bash
cd data && python3 ../figures/make_figures.py
```

Produces `fig1_inflation.pdf`, `fig2_topology.pdf` and `fig3_saving.pdf`
in single-column IEEE dimensions.

---

## A note on method

Results are reported as minima rather than medians. Timing noise on a
virtualised host is one-sided: a scheduling delay can only make a transfer
appear slower, never faster than the emulated path permits. Across three
pilot sessions the QUIC minimum held between 389 and 409 ms while the
median moved from 442 to 931 ms, because the distribution is bimodal and
the median lands on whichever cluster is larger that day. Reporting medians
would have produced three contradictory answers to one question.

Alongside each minimum the code reports how many runs fell within 15
percent of it, which is an honest indicator of how clean each measurement
session was.

---

## License

MIT for the code. The measurement data is derived from RIPE Atlas, which is
published under its own terms; see https://atlas.ripe.net for details.

## Citation

Please cite the paper. A BibTeX entry will be added once the article
appears.
