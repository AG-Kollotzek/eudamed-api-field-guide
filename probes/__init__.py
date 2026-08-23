"""Probe-Skripte für Phase 1.

Beantworten die offenen Fragen aus der Sichtung der Referenz-Repos, bevor der Client-Code
darauf aufbaut. Jede Probe stellt genau eine Frage und endet mit einem Verdikt.

Aufruf:
    python -m probes.run_probes            # alle
    python -m probes.run_probes --only 04  # nur die kritische
"""

from probes.base import ProbeResult, Verdict

__all__ = ["ProbeResult", "Verdict"]
