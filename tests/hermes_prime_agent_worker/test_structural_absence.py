"""Proves certain capabilities do not exist anywhere in this package's
governed control surface, real or fake -- introspecting the class/module
itself rather than a specific call site, following the convention
documented in ``hermes_docs_worker``'s own structural-absence tests
(``test_github_pr.py::test_gh_cli_client_has_no_merge_close_or_delete_capability``).

A future change that adds one of these methods should make this test fail
loudly, forcing a deliberate decision rather than an accidental capability
creep.
"""

from __future__ import annotations

import inspect

from hermes_prime_agent_worker import cli
from hermes_prime_agent_worker.sessions import PrimeAgentWorker

_FORBIDDEN_SESSION_METHOD_NAMES = (
    "merge",
    "merge_pr",
    "tag",
    "tag_release",
    "push",
    "release",
    "force_push",
    "delete_branch",
    "clear_kill_switch",
    "reset_kill_switch",
    "disable_kill_switch",
    "edit_systemd_unit",
    "enable_systemd_unit",
    "write_systemd_unit",
    "grant_sudo",
    "add_to_sudoers",
    "elevate_privileges",
)

_FORBIDDEN_CLI_SUBCOMMANDS = (
    "merge",
    "tag",
    "push",
    "release",
    "clear-kill-switch",
    "reset-kill-switch",
    "enable-unit",
    "edit-unit",
)


def test_prime_agent_worker_has_no_git_or_privilege_escalation_capability():
    members = {name for name, _ in inspect.getmembers(PrimeAgentWorker)}
    for forbidden in _FORBIDDEN_SESSION_METHOD_NAMES:
        assert forbidden not in members, (
            f"PrimeAgentWorker must not define {forbidden!r} -- this class only "
            "governs bounded Prime Agent worker sessions, never git history, "
            "release artifacts, its own systemd unit, or privilege escalation."
        )


def test_cli_has_no_forbidden_subcommands():
    parser = cli._build_parser()
    subparsers_action = next(
        action
        for action in parser._subparsers._group_actions  # type: ignore[union-attr]
        if hasattr(action, "choices")
    )
    choices = subparsers_action.choices
    assert choices is not None
    available = set(choices.keys())
    for forbidden in _FORBIDDEN_CLI_SUBCOMMANDS:
        assert forbidden not in available, (
            f"CLI must not expose a {forbidden!r} subcommand -- see "
            "PrimeAgentWorker's structural-absence guarantee."
        )


def test_prime_agent_worker_docstring_declares_the_absence_deliberately():
    assert "Deliberately absent" in (PrimeAgentWorker.__doc__ or "")
