# Step 29 system integration certification

Step 29 prepares deterministic, machine-readable evidence that the implemented
Steps 1-28 operate as one governed platform. It inventories the architecture,
checks cross-system identities and authority invariants, records a sanitized
evidence-chain manifest, exercises a local failure matrix, and prepares release
and rollback manifests.

The integration scenario is local and deterministic. Provider behavior,
fallback, remote maintenance, release preparation, and rollback preparation are
simulated through records and deterministic adapters. Step 29 does not contact a
provider, execute remote maintenance, merge, tag, release, deploy, or roll back.

Run the certification tests with:

```shell
.venv/bin/python -m pytest -q tests/hermes_cli/test_agent_roles/test_system_integration_certification.py
```

`ready_for_operator_review` means the declared suites and invariants passed but
still requires operator approval. `conditionally_ready` records advisory risks
or skipped checks. `blocked` records a blocking finding, dirty repository, or
failed suite; `failed` records a critical finding. Release preparation only
creates sanitized manifests. Merge, tag, release, deployment, destructive
actions, credential access, spending increases, and rollback remain operator
decisions.
