"""Real, evidence-backed ecosystem catalog seed entries.

Hermes add-on Phase G. Populates ``sigil.ecosystem_catalog`` -- previously
empty, per the audit ("zero populated entries exist today") -- with real
discovery records built from live GitHub metadata gathered during the
Paperclip/Buzz upstream search (Hermes add-on run, see
``docs/roadmap/HERMES_ADDON_STATUS.json`` item ``paperclip-upstream``).

Deliberately does not include a ``SECURITY_REVIEW`` evidence entry for any
seed here: a repository-metadata lookup is not a security review, and
claiming one happened would be dishonest evidence. Callers should expect
:func:`sigil.ecosystem_catalog.assess_discovery` to report
``evidence_complete=False`` / ``AdmissionReadiness.EVIDENCE_INCOMPLETE`` for
every entry below until a real security review is separately performed and
its own evidence recorded.
"""

from __future__ import annotations

from sigil.ecosystem_catalog import (
    DiscoveredIntegration,
    DiscoveryEvidence,
    DiscoverySourceKind,
)
from sigil.integration_registry import IntegrationCategory


def paperclip_discovery(observed_at: str) -> DiscoveredIntegration:
    """The identified Paperclip upstream (``paperclipai/paperclip``).

    Identification evidence: NousResearch -- the upstream maintainer
    organization of this very repository -- already publishes
    ``NousResearch/hermes-paperclip-adapter``, an MIT-licensed adapter
    whose own README states it lets Hermes Agent "run ... as a managed
    employee in a Paperclip company", cross-referencing
    ``paperclipai/paperclip`` (also MIT-licensed) by name and URL
    (paperclip.ing). Commits pinned below were HEAD of each repository's
    default branch at discovery time.
    """

    return DiscoveredIntegration(
        discovery_id="discovery-paperclip-2026-08-05",
        integration_id="paperclip",
        canonical_project_name="Paperclip",
        category=IntegrationCategory.ORGANIZATION,
        repository_url="https://github.com/paperclipai/paperclip",
        repository_identity="paperclipai/paperclip",
        pinned_identity="72b509c89539b31e421086c782346635c5d0517b",
        release_label=None,
        maintainer_identity="paperclipai (GitHub organization)",
        license_classification="MIT",
        maturity="active; created 2026-03-02, 75699 stargazers, 14093 forks, 5002 open issues as of discovery",
        capabilities=("agent_orchestration_ui", "work_assignment_tracking", "cost_reporting"),
        supported_machines=(),
        supported_profiles=(),
        known_risks=(
            "no independent security review performed by this project",
            "Node.js server component; not yet integration-tested against hermes_cli/prime",
            "high popularity metrics not independently corroborated beyond GitHub's own counters",
        ),
        threat_model_references=(),
        evidence=(
            DiscoveryEvidence(
                evidence_id="ev-paperclip-repo-snapshot",
                source_kind=DiscoverySourceKind.REPOSITORY_SNAPSHOT,
                observed_at=observed_at,
                source_identity="github.com/paperclipai/paperclip",
                content_digest="sha256:58b0c5f4b9b2f964dc6c7a098372c5d76e80d6eb3553b27bc310ec779d47cef7",
                provenance="gh api repos/paperclipai/paperclip (read-only metadata query)",
                reference="paperclip/repository-snapshot",
            ),
            DiscoveryEvidence(
                evidence_id="ev-paperclip-license-review",
                source_kind=DiscoverySourceKind.LICENSE_REVIEW,
                observed_at=observed_at,
                source_identity="github.com/paperclipai/paperclip/blob/master/LICENSE",
                content_digest="sha256:2c344dcb2a37b91a75189244aae5098c820828fe97fd420d7e11eff9c6caa318",
                provenance="gh api repos/paperclipai/paperclip licenseInfo field (MIT)",
                reference="paperclip/license-review",
            ),
            DiscoveryEvidence(
                evidence_id="ev-paperclip-activity-review",
                source_kind=DiscoverySourceKind.ACTIVITY_REVIEW,
                observed_at=observed_at,
                source_identity="github.com/paperclipai/paperclip",
                content_digest="sha256:a977c2f9999f9f65059f863c84aa38a722ef329216deb694f88c0ae752cca4c3",
                provenance="gh api repos/paperclipai/paperclip pushed_at/forks_count/open_issues_count",
                reference="paperclip/activity-review",
            ),
        ),
        observed_at=observed_at,
    )


def hermes_paperclip_adapter_discovery(observed_at: str) -> DiscoveredIntegration:
    """The upstream-maintained Hermes/Paperclip integration adapter.

    Published by NousResearch (this repository's own ``upstream`` git
    remote), not a third party -- the strongest form of upstream
    identification available short of an explicit operator confirmation.
    """

    return DiscoveredIntegration(
        discovery_id="discovery-hermes-paperclip-adapter-2026-08-05",
        integration_id="hermes-paperclip-adapter-upstream",
        canonical_project_name="Paperclip Adapter for Hermes Agent",
        category=IntegrationCategory.ORGANIZATION,
        repository_url="https://github.com/NousResearch/hermes-paperclip-adapter",
        repository_identity="NousResearch/hermes-paperclip-adapter",
        pinned_identity="937ea71a34f5efcaa3834b11fdd08cfc1c99cb2c",
        release_label=None,
        maintainer_identity="NousResearch (this repository's own upstream git remote)",
        license_classification="MIT",
        maturity="published by the upstream maintainer of hermes-agent; last pushed 2026-04-04",
        capabilities=("agent_orchestration_ui", "skills_sync", "session_transcript_parsing"),
        supported_machines=(),
        supported_profiles=(),
        known_risks=(
            "no independent security review performed by this project",
            "npm package (hermes-paperclip-adapter); not yet integration-tested against this fork's hermes_cli/prime fleet layer",
            "written for upstream NousResearch/hermes-agent's CLI surface, not this fork's specific customizations",
        ),
        threat_model_references=(),
        evidence=(
            DiscoveryEvidence(
                evidence_id="ev-hpa-repo-snapshot",
                source_kind=DiscoverySourceKind.REPOSITORY_SNAPSHOT,
                observed_at=observed_at,
                source_identity="github.com/NousResearch/hermes-paperclip-adapter",
                content_digest="sha256:2cc392b906079ad07d085798c077969bed59feccc57e947a9a71fee5f53729fe",
                provenance="gh api repos/NousResearch/hermes-paperclip-adapter (read-only metadata query)",
                reference="hermes-paperclip-adapter/repository-snapshot",
            ),
            DiscoveryEvidence(
                evidence_id="ev-hpa-license-review",
                source_kind=DiscoverySourceKind.LICENSE_REVIEW,
                observed_at=observed_at,
                source_identity="github.com/NousResearch/hermes-paperclip-adapter",
                content_digest="sha256:5dc247e74eae03a84920107311a4ddef1472848b408162078280705613f64138",
                provenance="gh api repos/NousResearch/hermes-paperclip-adapter licenseInfo field (MIT)",
                reference="hermes-paperclip-adapter/license-review",
            ),
            DiscoveryEvidence(
                evidence_id="ev-hpa-activity-review",
                source_kind=DiscoverySourceKind.ACTIVITY_REVIEW,
                observed_at=observed_at,
                source_identity="github.com/NousResearch/hermes-paperclip-adapter",
                content_digest="sha256:99a145737ffb9d36bb3c9d4d7414b294c7cda285d84c286aacd9e2d5b38b79e1",
                provenance="gh api repos/NousResearch/hermes-paperclip-adapter pushed_at field",
                reference="hermes-paperclip-adapter/activity-review",
            ),
        ),
        observed_at=observed_at,
    )


def seed_discoveries(observed_at: str) -> tuple[DiscoveredIntegration, ...]:
    """Every real, evidence-backed discovery entry known at this time.

    Buzz is deliberately absent: no credible upstream repository was found
    (searched NousResearch's own org, GitHub full-text/topic search, and
    general web search) as of this discovery run, so there is nothing real
    to seed -- fabricating a placeholder entry would be worse than leaving
    the gap visible.
    """

    return (
        paperclip_discovery(observed_at),
        hermes_paperclip_adapter_discovery(observed_at),
    )
