"""Read-only evidence collectors.

Every collector in this package follows the same contract:

- ``collect(config, ...) -> Tuple[EvidenceFact, ...]`` -- never raises for
  an expected runtime condition (a service not installed, an endpoint
  unreachable, a file missing); those become ``Unknown``/``Degraded``/
  ``Blocked`` facts with a redacted detail string. A collector only raises
  for a genuine programming/configuration bug (e.g. being asked to read a
  path outside the configured filesystem allowlist).
- No collector mutates anything. No collector executes vault content,
  model output, or any other untrusted text as a command.
- No collector accesses the Mac; Prime/Mac/Hydra Live status is only ever
  read through the existing governed fleet registry
  (:mod:`hermes_docs_worker.collectors.fleet_status`), never a fresh
  network probe this worker invents.
"""

from __future__ import annotations
