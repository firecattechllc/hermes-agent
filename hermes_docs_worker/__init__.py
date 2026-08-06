"""Titan governed documentation worker.

Periodically collects verified local/fleet evidence on Titan and drafts
conservative Markdown updates to the private ``firecattechllc/hydra-docs``
Obsidian vault, opening a reviewable GitHub pull request when a run produces
a meaningful documentation change. This package never merges a pull
request, never pushes to ``main``, never deletes a remote branch, and never
tags a release -- see :mod:`hermes_docs_worker.git_ops` and
:mod:`hermes_docs_worker.github_pr`, where those actions are structurally
absent rather than merely policy-disabled.
"""

from __future__ import annotations

__version__ = "0.1.0"
