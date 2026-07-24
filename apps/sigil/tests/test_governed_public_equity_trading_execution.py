from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib
import inspect
import json
import socket
from urllib.error import HTTPError, URLError
from uuid import UUID

import pytest

from sigil.integrations.providers import (
    PUBLIC_ALLOWED_HOSTS,
    PUBLIC_API_SECRET_ENVIRONMENT_VARIABLE,
    PUBLIC_EXECUTION_ADAPTER_VERSION,
    PUBLIC_EXECUTION_PROVIDER_ID,
    PUBLIC_EXECUTION_SUPPORTED_OPERATIONS,
    PUBLIC_FORBIDDEN_CAPABILITIES,
    FinancialDataAuthenticationError,
    FinancialDataRateLimitError,
    FinancialDataTransportError,
    FinancialDataValidationError,
    GovernedEquityTradeProposal,
    GovernedTradeApproval,
    MappingCredentialResolver,
    PublicAccessTokenManager,
    PublicCancellationApproval,
    PublicEquityExecutionProvider,
    PublicExecutionJournal,
    PublicExecutionPolicy,
    PublicExecutionState,
    normalize_public_account_id,
    protected_account_binding,
)
from sigil.integrations.providers.public_execution import _PublicGovernedTransport


NOW = datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
ACCOUNT_ID = "acct_test_123"
SECRET = "fictional-public-secret-for-offline-test"
TOKEN = "fictional-temporary-access-token"
ORDER_ID = "123e4567-e89b-42d3-a456-426614174000"


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden in Step 9B tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


class FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self.raw = b"" if payload is None else json.dumps(payload).encode()
        self.status = status
        self.headers = {"Content-Type": content_type}

    def getcode(self) -> int:
        return self.status

    def read(self, size: int) -> bytes:
        return self.raw[:size]


class RecordingOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[object] = []
        self.observer = None

    def __call__(self, request: object, timeout: float) -> object:
        self.requests.append(request)
        if self.observer is not None:
            self.observer()
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def policy(**changes: object) -> PublicExecutionPolicy:
    values: dict[str, object] = {
        "maximum_notional_per_order": "1000",
        "maximum_whole_share_quantity": "100",
        "maximum_fractional_notional": "500",
        "allowed_symbols": ("AAPL", "SPY"),
        "proposal_lifetime_seconds": 600,
        "approval_lifetime_seconds": 300,
        "preflight_lifetime_seconds": 120,
        "portfolio_freshness_seconds": 30,
    }
    values.update(changes)
    return PublicExecutionPolicy(**values)  # type: ignore[arg-type]


def proposal(
    *,
    execution_policy: PublicExecutionPolicy | None = None,
    side: str = "BUY",
    order_type: str = "LIMIT",
    quantity: object = "2",
    notional_amount: object = None,
    limit_price: object = "100",
    created_at: datetime = NOW,
    **changes: object,
) -> GovernedEquityTradeProposal:
    values: dict[str, object] = {
        "policy": execution_policy or policy(),
        "account_id": ACCOUNT_ID,
        "symbol": "aapl",
        "side": side,
        "order_type": order_type,
        "quantity": quantity,
        "notional_amount": notional_amount,
        "limit_price": limit_price,
        "purpose": "explicit operator-requested test trade",
        "created_at": created_at,
        "correlation_id": "corr-public-test",
        "requested_by": "operator-test",
    }
    values.update(changes)
    return GovernedEquityTradeProposal.create(**values)  # type: ignore[arg-type]


def portfolio_payload(*, cash: str = "500", quantity: str = "10") -> dict[str, object]:
    return {
        "accountId": ACCOUNT_ID,
        "buyingPower": {"cashOnlyBuyingPower": cash},
        "positions": [
            {
                "instrument": {"symbol": "AAPL", "type": "EQUITY"},
                "quantity": quantity,
            }
        ],
    }


def preflight_payload(requirement: str = "200") -> dict[str, object]:
    return {
        "estimatedCost": "200.00",
        "buyingPowerRequirement": requirement,
        "regulatoryFees": {"secFee": "0.00"},
    }


def make_provider(
    outcomes: list[object],
    *,
    execution_policy: PublicExecutionPolicy | None = None,
    clock: list[float] | None = None,
    wall_now: list[datetime] | None = None,
    journal: PublicExecutionJournal | None = None,
) -> tuple[PublicEquityExecutionProvider, RecordingOpener, PublicExecutionJournal]:
    opener = RecordingOpener(outcomes)
    transport = _PublicGovernedTransport(opener=opener)
    monotonic = clock or [0.0]
    token_manager = PublicAccessTokenManager(
        credential_resolver=MappingCredentialResolver(
            {PUBLIC_EXECUTION_PROVIDER_ID: SECRET}
        ),
        transport=transport,
        monotonic=lambda: monotonic[0],
    )
    records = journal or PublicExecutionJournal()
    provider = PublicEquityExecutionProvider(
        token_manager=token_manager,
        transport=transport,
        policy=execution_policy if execution_policy is not None else policy(),
        journal=records,
        wall_clock=lambda: (wall_now or [NOW])[0],
        order_id_factory=lambda: UUID(ORDER_ID),
    )
    return provider, opener, records


def perform_preflight(
    provider: PublicEquityExecutionProvider, trade: GovernedEquityTradeProposal
):
    return provider.preflight(trade)


def approval_for(trade, preflight, **changes):
    values = {
        "proposal": trade,
        "preflight": preflight,
        "maximum_authorized_notional": "300",
        "approved_at": NOW,
        "approved_by": "human-operator",
        "single_use_nonce": "unique-approval-nonce",
        "policy": policy(),
    }
    values.update(changes)
    return GovernedTradeApproval.create(**values)


def test_exact_provider_metadata_and_forbidden_scope() -> None:
    assert PUBLIC_EXECUTION_PROVIDER_ID == "public_equity_execution"
    assert PUBLIC_EXECUTION_ADAPTER_VERSION == "sigil-public-equity-execution-v1"
    assert PUBLIC_ALLOWED_HOSTS == ("api.public.com",)
    assert PUBLIC_EXECUTION_SUPPORTED_OPERATIONS == tuple(
        sorted(PUBLIC_EXECUTION_SUPPORTED_OPERATIONS)
    )
    assert {
        "order_replacement",
        "options",
        "crypto",
        "bonds",
        "short_selling",
        "margin",
        "hosted_mcp",
    } <= set(PUBLIC_FORBIDDEN_CAPABILITIES)


def test_missing_secret_fails_before_network() -> None:
    opener = RecordingOpener([])
    transport = _PublicGovernedTransport(opener=opener)
    manager = PublicAccessTokenManager(
        credential_resolver=MappingCredentialResolver({}),
        transport=transport,
    )
    with pytest.raises(FinancialDataAuthenticationError):
        manager.get()
    assert opener.requests == []


def test_authentication_endpoint_body_and_token_privacy() -> None:
    provider, opener, _ = make_provider(
        [FakeResponse({"accessToken": TOKEN}), FakeResponse({"accounts": []})]
    )
    assert provider.list_accounts() == {"accounts": []}
    request = opener.requests[0]
    assert request.full_url == (  # type: ignore[attr-defined]
        "https://api.public.com/userapiauthservice/personal/access-tokens"
    )
    assert request.method == "POST"  # type: ignore[attr-defined]
    assert json.loads(request.data) == {  # type: ignore[attr-defined]
        "secret": SECRET,
        "validityInMinutes": 15,
    }
    assert request.get_header("Authorization") is None  # type: ignore[attr-defined]
    exposed = repr(provider.health()) + repr(provider._tokens)  # type: ignore[attr-defined]
    assert SECRET not in exposed
    assert TOKEN not in exposed


@pytest.mark.parametrize("payload", [{}, {"accessToken": ""}, [], {"accessToken": 1}])
def test_malformed_token_response_fails_closed_without_leak(payload: object) -> None:
    provider, _, _ = make_provider([FakeResponse(payload)])
    with pytest.raises(FinancialDataAuthenticationError) as caught:
        provider.list_accounts()
    assert SECRET not in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_token_reuse_then_refresh_after_expiry() -> None:
    clock = [0.0]
    provider, opener, _ = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse({"accounts": []}),
            FakeResponse({"accounts": []}),
            FakeResponse({"accessToken": "second-token"}),
            FakeResponse({"accounts": []}),
        ],
        clock=clock,
    )
    provider.list_accounts()
    provider.list_accounts()
    assert [request.method for request in opener.requests] == ["POST", "GET", "GET"]
    clock[0] = 871.0
    provider.list_accounts()
    assert [request.method for request in opener.requests] == [
        "POST",
        "GET",
        "GET",
        "POST",
        "GET",
    ]


def test_health_is_local_private_and_execution_policy_gated() -> None:
    provider, opener, _ = make_provider([])
    health = provider.health()
    assert opener.requests == []
    assert health.runtime_secret_available is True
    assert health.token_present is False
    assert health.policy_configured is True
    assert health.execution_enabled is True
    provider._policy = None  # type: ignore[attr-defined]
    assert provider.health().execution_enabled is False


@pytest.mark.parametrize(
    "value",
    ["", "has space", "slash/value", "query?x", "fragment#x", "../x", "é", "x" * 129],
)
def test_account_id_validation(value: str) -> None:
    with pytest.raises(FinancialDataValidationError):
        normalize_public_account_id(value)
    assert ACCOUNT_ID not in protected_account_binding(ACCOUNT_ID)


def test_proposal_is_deterministic_immutable_and_decimal_safe() -> None:
    first = proposal(quantity=Decimal("2.00"))
    second = proposal(quantity="2")
    assert first.proposal_id == second.proposal_id
    assert first.quantity == "2"
    assert ACCOUNT_ID not in first.proposal_id
    assert SECRET not in first.proposal_id
    with pytest.raises(FrozenInstanceError):
        first.symbol = "SPY"  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"quantity": 1.0},
        {"quantity": "NaN"},
        {"quantity": "Infinity"},
        {"quantity": "1e2"},
        {"quantity": "0"},
        {"quantity": "-1"},
        {"quantity": "1", "notional_amount": "2"},
        {"quantity": None, "notional_amount": None},
        {"order_type": "MARKET", "limit_price": "1"},
        {"order_type": "LIMIT", "limit_price": None},
        {"instrument_type": "OPTION"},
        {"side": "SHORT"},
        {"order_type": "STOP"},
        {"time_in_force": "GTC"},
        {"market_session": "EXTENDED"},
        {"use_margin": True},
    ],
)
def test_proposal_rejects_unsafe_or_ambiguous_terms(kwargs: dict[str, object]) -> None:
    if kwargs.get("order_type") == "MARKET" and "limit_price" not in kwargs:
        kwargs["limit_price"] = None
    with pytest.raises(FinancialDataValidationError):
        proposal(**kwargs)


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum_notional_per_order": "0"},
        {"maximum_whole_share_quantity": "Infinity"},
        {"allowed_symbols": ()},
        {"require_explicit_approval": False},
        {"prohibit_margin": False},
    ],
)
def test_policy_has_no_unlimited_or_fail_open_mode(changes: dict[str, object]) -> None:
    with pytest.raises(FinancialDataValidationError):
        policy(**changes)


def test_policy_caps_and_missing_policy_fail_closed() -> None:
    with pytest.raises(FinancialDataValidationError):
        proposal(execution_policy=policy(maximum_whole_share_quantity="1"), quantity="2")
    provider, _, _ = make_provider([], execution_policy=policy())
    provider._policy = None  # type: ignore[attr-defined]
    with pytest.raises(FinancialDataValidationError):
        provider.preflight(proposal())


def test_preflight_exact_portfolio_and_order_request_and_binding() -> None:
    provider, opener, _ = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
        ]
    )
    trade = proposal()
    record = perform_preflight(provider, trade)
    assert [request.method for request in opener.requests] == ["POST", "GET", "POST"]
    assert opener.requests[1].full_url.endswith(  # type: ignore[attr-defined]
        f"/userapigateway/trading/{ACCOUNT_ID}/portfolio/v2"
    )
    assert opener.requests[2].full_url.endswith(  # type: ignore[attr-defined]
        f"/userapigateway/trading/{ACCOUNT_ID}/preflight/single-leg"
    )
    assert json.loads(opener.requests[2].data) == {  # type: ignore[attr-defined]
        "equityMarketSession": "CORE",
        "expiration": {"timeInForce": "DAY"},
        "instrument": {"symbol": "AAPL", "type": "EQUITY"},
        "limitPrice": "100",
        "orderSide": "BUY",
        "orderType": "LIMIT",
        "quantity": "2",
        "useMargin": False,
    }
    assert record.proposal_hash == trade.proposal_hash
    assert record.estimated_cost == "200"
    assert record.regulatory_fees == {"secFee": "0.00"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"buyingPowerRequirement": "NaN"},
        {"buyingPowerRequirement": "1", "outcome": "REJECTED"},
        {"buyingPowerRequirement": "1", "rejectionReason": "no"},
    ],
)
def test_preflight_malformed_or_rejected_fails_closed(payload: object) -> None:
    provider, _, _ = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(payload),
        ]
    )
    with pytest.raises(FinancialDataValidationError):
        perform_preflight(provider, proposal())


def test_buy_requires_fresh_cash_and_no_silent_resize() -> None:
    provider, _, _ = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload(cash="100")),
            FakeResponse(preflight_payload("200")),
        ]
    )
    with pytest.raises(FinancialDataValidationError, match="insufficient"):
        perform_preflight(provider, proposal())


def test_sell_requires_sufficient_long_holdings_and_rejects_notional() -> None:
    provider, _, _ = make_provider(
        [FakeResponse({"accessToken": TOKEN}), FakeResponse(portfolio_payload(quantity="1"))]
    )
    with pytest.raises(FinancialDataValidationError, match="holdings"):
        perform_preflight(provider, proposal(side="SELL", quantity="2"))
    with pytest.raises(FinancialDataValidationError):
        proposal(side="SELL", quantity=None, notional_amount="20")


def test_expired_proposal_and_preflight_and_approval_fail() -> None:
    wall = [NOW]
    provider, _, _ = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
        ],
        wall_now=wall,
    )
    trade = proposal()
    preflight = perform_preflight(provider, trade)
    approval = approval_for(trade, preflight)
    wall[0] = NOW + timedelta(minutes=11)
    with pytest.raises(FinancialDataValidationError, match="expired"):
        provider.submit_approved_equity_order(trade, preflight, approval)


def test_exact_approval_and_submission_intent_precede_network() -> None:
    provider, opener, journal = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
            FakeResponse({"orderId": ORDER_ID}),
        ]
    )
    trade = proposal()
    preflight = perform_preflight(provider, trade)
    approval = approval_for(trade, preflight)

    def assert_recorded() -> None:
        if len(opener.requests) == 4:
            assert journal.intent(f"public-intent-{next(iter(journal._intents)).removeprefix('public-intent-')}")  # type: ignore[attr-defined]

    opener.observer = assert_recorded
    execution = provider.submit_approved_equity_order(trade, preflight, approval)
    request = opener.requests[-1]
    assert request.method == "POST"  # type: ignore[attr-defined]
    assert request.full_url.endswith(  # type: ignore[attr-defined]
        f"/userapigateway/trading/{ACCOUNT_ID}/order"
    )
    body = json.loads(request.data)  # type: ignore[attr-defined]
    assert body["orderId"] == ORDER_ID
    assert execution.state == PublicExecutionState.SUBMITTED
    assert execution.state != PublicExecutionState.FILLED
    assert ACCOUNT_ID not in repr(journal.evidence())


def test_missing_modified_and_replayed_approval_rejected() -> None:
    provider, _, _ = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
            FakeResponse({"orderId": ORDER_ID}),
        ]
    )
    trade = proposal()
    preflight = perform_preflight(provider, trade)
    approval = approval_for(trade, preflight)
    with pytest.raises(FinancialDataValidationError, match="structured"):
        provider.submit_approved_equity_order(trade, preflight, None)  # type: ignore[arg-type]
    with pytest.raises(FinancialDataValidationError):
        replace(approval, symbol="SPY")
    provider.submit_approved_equity_order(trade, preflight, approval)
    with pytest.raises(FinancialDataValidationError, match="consumed"):
        provider.submit_approved_equity_order(trade, preflight, approval)


@pytest.mark.parametrize("artifact", ["approval", "preflight"])
def test_modified_governance_artifact_fails_before_private_transport(
    artifact: str,
) -> None:
    provider, opener, _ = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
        ]
    )
    trade = proposal()
    preflight = perform_preflight(provider, trade)
    approval = approval_for(trade, preflight)
    if artifact == "approval":
        object.__setattr__(approval, "symbol", "SPY")
    else:
        object.__setattr__(preflight, "proposal_hash", "0" * 64)
    request_count = len(opener.requests)

    with pytest.raises(FinancialDataValidationError):
        provider.submit_approved_equity_order(trade, preflight, approval)

    assert len(opener.requests) == request_count


def test_governed_submission_api_accepts_no_account_body_or_token() -> None:
    parameters = inspect.signature(
        PublicEquityExecutionProvider.submit_approved_equity_order
    ).parameters
    assert set(parameters) == {"self", "proposal", "preflight", "approval"}
    assert {"account_id", "body", "token"}.isdisjoint(parameters)


def test_ambiguous_submission_reuses_same_order_id_and_body() -> None:
    provider, opener, _ = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
            URLError("timeout"),
            FakeResponse({"orderId": ORDER_ID}),
        ]
    )
    trade = proposal()
    preflight = perform_preflight(provider, trade)
    approval = approval_for(trade, preflight)
    with pytest.raises(FinancialDataTransportError):
        provider.submit_approved_equity_order(trade, preflight, approval)
    execution = provider._journal.execution(ORDER_ID)  # type: ignore[attr-defined]
    assert execution.state == PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED
    retried = provider.retry_ambiguous_submission(
        account_id=ACCOUNT_ID, intent_id=execution.intent_id, proposal=trade
    )
    first_body = opener.requests[-2].data  # type: ignore[attr-defined]
    second_body = opener.requests[-1].data  # type: ignore[attr-defined]
    assert first_body == second_body
    assert json.loads(second_body)["orderId"] == ORDER_ID
    assert len(provider._journal._intents) == 1  # type: ignore[attr-defined]
    assert retried.state == PublicExecutionState.ACKNOWLEDGED


@pytest.mark.parametrize("retry_account_id", ["acct_other_456", "../other"])
def test_ambiguous_retry_rejects_wrong_or_malformed_account_before_authentication(
    retry_account_id: str,
) -> None:
    provider, opener, journal = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
            URLError("timeout"),
        ]
    )
    trade = proposal()
    preflight = perform_preflight(provider, trade)
    with pytest.raises(FinancialDataTransportError):
        provider.submit_approved_equity_order(
            trade, preflight, approval_for(trade, preflight)
        )
    execution = journal.execution(ORDER_ID)
    intent_count = len(journal._intents)  # type: ignore[attr-defined]
    evidence = journal.evidence()
    provider._tokens.invalidate()  # type: ignore[attr-defined]
    request_count = len(opener.requests)

    with pytest.raises(FinancialDataValidationError) as caught:
        provider.retry_ambiguous_submission(
            account_id=retry_account_id,
            intent_id=execution.intent_id,
            proposal=trade,
        )

    assert len(opener.requests) == request_count
    assert len(journal._intents) == intent_count  # type: ignore[attr-defined]
    assert journal.execution(ORDER_ID) == execution
    assert journal.evidence() == evidence
    assert ACCOUNT_ID not in str(caught.value)
    assert retry_account_id not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, FinancialDataAuthenticationError), (403, FinancialDataAuthenticationError), (429, FinancialDataRateLimitError)],
)
def test_execution_errors_are_safe(status: int, error: type[Exception]) -> None:
    provider, _, _ = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
            FakeResponse({}, status=status),
        ]
    )
    trade = proposal()
    preflight = perform_preflight(provider, trade)
    with pytest.raises(error) as caught:
        provider.submit_approved_equity_order(trade, preflight, approval_for(trade, preflight))
    text = str(caught.value)
    assert SECRET not in text and TOKEN not in text and ACCOUNT_ID not in text


def _submitted_provider(status_payload: object):
    status_outcome = (
        status_payload
        if isinstance(status_payload, BaseException)
        else FakeResponse(status_payload)
    )
    provider, opener, journal = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
            FakeResponse({"orderId": ORDER_ID}),
            status_outcome,
        ]
    )
    trade = proposal()
    preflight = perform_preflight(provider, trade)
    provider.submit_approved_equity_order(trade, preflight, approval_for(trade, preflight))
    return provider, opener, journal


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("NEW", PublicExecutionState.ACKNOWLEDGED),
        ("PARTIALLY_FILLED", PublicExecutionState.PARTIALLY_FILLED),
        ("FILLED", PublicExecutionState.FILLED),
        ("REJECTED", PublicExecutionState.REJECTED),
    ],
)
def test_order_status_mapping_and_exact_get(status: str, expected: PublicExecutionState) -> None:
    provider, opener, _ = _submitted_provider({"orderId": ORDER_ID, "status": status})
    execution = provider.get_order(account_id=ACCOUNT_ID, order_id=ORDER_ID)
    assert execution.state == expected
    assert opener.requests[-1].method == "GET"  # type: ignore[attr-defined]
    assert opener.requests[-1].full_url.endswith(ORDER_ID)  # type: ignore[attr-defined]


def test_immediate_404_requires_reconciliation_without_resubmission() -> None:
    not_found = HTTPError("url", 404, "not found", {}, None)
    provider, opener, _ = _submitted_provider(not_found)
    execution = provider.get_order(account_id=ACCOUNT_ID, order_id=ORDER_ID)
    assert execution.state == PublicExecutionState.UNKNOWN_RECONCILIATION_REQUIRED
    assert sum(request.method == "POST" for request in opener.requests) == 3  # type: ignore[attr-defined]


def test_cancellation_requires_separate_exact_approval_and_later_reconciliation() -> None:
    provider, opener, _ = _submitted_provider({"orderId": ORDER_ID, "status": "NEW"})
    execution = provider.get_order(account_id=ACCOUNT_ID, order_id=ORDER_ID)
    approval = PublicCancellationApproval.create(
        order_id=ORDER_ID,
        account_id=ACCOUNT_ID,
        approved_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
        approved_by="human-operator",
        single_use_nonce="cancel-once",
    )
    opener.outcomes.append(FakeResponse(None))
    cancelled = provider.cancel_approved_order(
        account_id=ACCOUNT_ID, order_id=ORDER_ID, approval=approval
    )
    assert execution.state == PublicExecutionState.ACKNOWLEDGED
    assert cancelled.state == PublicExecutionState.CANCELLATION_REQUESTED
    assert opener.requests[-1].method == "DELETE"  # type: ignore[attr-defined]
    with pytest.raises(FinancialDataValidationError, match="consumed"):
        provider.cancel_approved_order(
            account_id=ACCOUNT_ID, order_id=ORDER_ID, approval=approval
        )


def test_terminal_order_cannot_be_cancelled() -> None:
    provider, _, _ = _submitted_provider({"orderId": ORDER_ID, "status": "FILLED"})
    provider.get_order(account_id=ACCOUNT_ID, order_id=ORDER_ID)
    approval = PublicCancellationApproval.create(
        order_id=ORDER_ID,
        account_id=ACCOUNT_ID,
        approved_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
        approved_by="human",
        single_use_nonce="cancel",
    )
    with pytest.raises(FinancialDataValidationError, match="terminal"):
        provider.cancel_approved_order(
            account_id=ACCOUNT_ID, order_id=ORDER_ID, approval=approval
        )


def test_transport_rejects_redirect_non_json_oversize_and_wrong_ids() -> None:
    redirect = HTTPError("url", 302, "redirect", {"Location": "https://evil.invalid"}, None)
    provider, _, _ = make_provider([redirect])
    with pytest.raises(FinancialDataTransportError, match="302"):
        provider.list_accounts()
    provider, _, _ = make_provider(
        [FakeResponse({"accessToken": TOKEN}), FakeResponse({}, content_type="text/plain")]
    )
    with pytest.raises(FinancialDataValidationError, match="application/json"):
        provider.list_accounts()
    with pytest.raises(FinancialDataValidationError):
        PublicCancellationApproval.create(
            order_id="not-uuid",
            account_id=ACCOUNT_ID,
            approved_at=NOW,
            expires_at=NOW + timedelta(seconds=1),
            approved_by="human",
            single_use_nonce="nonce",
        )


def test_no_generic_transport_request_surface() -> None:
    transport = _PublicGovernedTransport(opener=RecordingOpener([]))
    public_methods = {name for name in dir(transport) if not name.startswith("_")}
    assert "request" not in public_methods
    assert "put" not in public_methods
    assert "replace_order" not in public_methods
    assert "submit_order" not in public_methods
    assert {"authenticate", "preflight", "cancel_order"} <= public_methods


def test_public_package_excludes_raw_execution_transport() -> None:
    providers = importlib.import_module("sigil.integrations.providers")
    assert not hasattr(providers, "PublicGovernedTransport")
    assert "PublicGovernedTransport" not in providers.__all__
    assert hasattr(providers, "PublicEquityExecutionProvider")
    assert not any("submit_order" in name.lower() for name in providers.__all__)


def test_representations_exclude_secrets_tokens_raw_account_and_nonce() -> None:
    trade = proposal()
    provider, _, journal = make_provider(
        [
            FakeResponse({"accessToken": TOKEN}),
            FakeResponse(portfolio_payload()),
            FakeResponse(preflight_payload()),
        ]
    )
    preflight = perform_preflight(provider, trade)
    approval = approval_for(trade, preflight)
    text = repr(trade) + repr(preflight) + repr(approval) + repr(journal.evidence())
    assert SECRET not in text
    assert TOKEN not in text
    assert ACCOUNT_ID not in text
    assert "unique-approval-nonce" not in text
    assert "Authorization" not in text
    assert PUBLIC_API_SECRET_ENVIRONMENT_VARIABLE not in trade.proposal_id
