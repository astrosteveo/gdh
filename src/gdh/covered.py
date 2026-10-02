"""Whether a large UI panel covered the middle of the screen for most of a recording (a modal left up, say).

harness/covered.gd samples, every few frames, the panels that draw over the screen's centre (see there for what counts
as one); this turns the samples into findings. A panel there for at least MOST_OF the samples is flagged.
"""

MOST_OF = 0.5


def covered_findings(samples):
    """Findings, in the probes' shape, for each panel over the centre in at least MOST_OF of samples (a list, per
    sample, of {node, class, share, rect})."""
    if not samples:
        return []
    seen = {}
    for sample in samples:
        for panel in sample:
            entry = seen.setdefault(panel["node"], {**panel, "count": 0, "max_share": 0.0})
            entry["count"] += 1
            if panel["share"] >= entry["max_share"]:
                entry.update(max_share=panel["share"], rect=panel["rect"])
    findings = []
    for node, e in seen.items():
        time_share = e["count"] / len(samples)
        if time_share < MOST_OF:
            continue
        findings.append({
            "probe": "covered",
            "severity": "warning",
            "node": node,
            "message": f"{e['class']} covered {e['max_share']:.0%} of the screen, its centre included, in "
                       f"{time_share:.0%} of the recording's samples: the game was hidden behind it.",
            "data": {"class": e["class"], "screen_share": e["max_share"], "time_share": round(time_share, 3),
                     "samples": len(samples)},
            "screen_rect": e["rect"],
            "view": "normal",
        })
    return sorted(findings, key=lambda f: -f["data"]["time_share"])
