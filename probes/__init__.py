"""Probe scripts that measure the observable behaviour of the EUDAMED API.

Each probe asks exactly one question and ends with a verdict.

Usage:
    python -m probes.run_probes            # all probes
    python -m probes.run_probes --only 04  # a single probe
"""

from probes.base import ProbeResult, Verdict

__all__ = ["ProbeResult", "Verdict"]
