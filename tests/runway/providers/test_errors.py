from __future__ import annotations

from runway.providers.errors import ExecutionFailureCategory, to_task_outcome_flags


def test_network_and_timeout_map_to_network_failure():
    for category in (ExecutionFailureCategory.NETWORK_FAILURE, ExecutionFailureCategory.TIMEOUT):
        flags = to_task_outcome_flags(category.value)
        assert flags == {"network_failure": True, "provider_error": False, "validation_failure": False}


def test_auth_and_rate_limit_and_provider_error_map_to_provider_error():
    for category in (ExecutionFailureCategory.AUTH_FAILED, ExecutionFailureCategory.RATE_LIMITED,
                      ExecutionFailureCategory.PROVIDER_ERROR, ExecutionFailureCategory.MALFORMED_RESPONSE):
        flags = to_task_outcome_flags(category.value)
        assert flags == {"network_failure": False, "provider_error": True, "validation_failure": False}


def test_invalid_request_and_not_authorized_map_to_validation_failure():
    for category in (ExecutionFailureCategory.INVALID_REQUEST, ExecutionFailureCategory.EXECUTION_NOT_AUTHORIZED):
        flags = to_task_outcome_flags(category.value)
        assert flags == {"network_failure": False, "provider_error": False, "validation_failure": True}


def test_success_and_unknown_strings_map_to_all_false():
    assert to_task_outcome_flags("") == {"network_failure": False, "provider_error": False, "validation_failure": False}
    assert to_task_outcome_flags("some_unmapped_string") == {
        "network_failure": False, "provider_error": False, "validation_failure": False,
    }
