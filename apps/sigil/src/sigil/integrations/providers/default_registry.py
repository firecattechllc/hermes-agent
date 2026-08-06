"""The one real construction site wiring SEC EDGAR and FRED into a shared registry.

Hermes add-on continuation run. Per the audit, neither provider was
imported by any consumer -- there was no shared registry construction
point at all, only the providers themselves plus their own tests. This
module is that point: a single, explicit, dependency-injected
``FinancialDataProviderRegistry`` containing both. A future research/
valuation pipeline integration should depend on this function rather than
constructing its own registry, so there remains exactly one place that
decides which financial-data providers exist.

Both providers stay exactly as governed as before: SEC EDGAR requires no
credential (a descriptive User-Agent only, per SEC's own policy); FRED
requires a real API key resolved through ``identity_resolver`` and is
simply absent from ``health()`` results as "configured" until one is
supplied. This module fabricates no credential and performs no network
call itself.
"""

from __future__ import annotations

from .fred import FRED_API_KEY_ENVIRONMENT_VARIABLE, FRED_PROVIDER_ID, FredProvider
from .registry import FinancialDataProviderRegistry
from .sec_edgar import SEC_PROVIDER_ID, SEC_USER_AGENT_ENVIRONMENT_VARIABLE, SECEdgarProvider
from .transport import CredentialResolver, EnvironmentCredentialResolver


def build_default_financial_data_registry(
    *, identity_resolver: CredentialResolver | None = None
) -> FinancialDataProviderRegistry:
    """Construct the one shared registry containing SEC EDGAR and FRED.

    ``identity_resolver`` defaults to :class:`EnvironmentCredentialResolver`,
    which reads real environment variables only (``SIGIL_SEC_USER_AGENT``,
    ``SIGIL_FRED_API_KEY``) and never fabricates a value.
    """

    resolver = identity_resolver or EnvironmentCredentialResolver(
        {
            SEC_PROVIDER_ID: SEC_USER_AGENT_ENVIRONMENT_VARIABLE,
            FRED_PROVIDER_ID: FRED_API_KEY_ENVIRONMENT_VARIABLE,
        }
    )
    return FinancialDataProviderRegistry(
        (
            SECEdgarProvider(identity_resolver=resolver),
            FredProvider(identity_resolver=resolver),
        )
    )
