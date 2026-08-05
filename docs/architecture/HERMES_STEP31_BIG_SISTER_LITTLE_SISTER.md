# Hermes Step 31 — Big Sister / Little Sister Learning Hierarchy

## Purpose

Step 31 establishes a governed mentorship architecture between:

- **Mac Hermes — Big Sister**, the senior engineering, teaching, and
  lesson-creation node.
- **Titan Hermes — Little Sister**, the always-on nursery, practice, and
  operational node.

Titan remains capable of safe independent operation when Mac is unavailable.

## Routing hierarchy

A learning request is considered in this order:

1. Local memory and existing governed lessons.
2. Local Ollama reasoning.
3. FinBERT for financial-sentiment tasks.
4. FreeLLMAPI when remote-gateway policy permits.
5. Mac Big Sister teaching when available.
6. A cloud specialist only through existing approval and spending controls.
7. A durable deferred lesson request when no eligible route remains.

Financial-sentiment tasks may select FinBERT before general local reasoning
after local-memory retrieval has been attempted.

## Authority boundary

The Step 31 subsystem is decision-and-evidence only.

It does not:

- execute a model;
- contact a node;
- transmit task contents;
- modify model or provider configuration;
- increase a budget;
- grant approval;
- alter governance policy;
- run arbitrary commands;
- deploy a lesson automatically.

Every learning decision sets `execution_permitted` to `false`.

## Offline behavior

Big Sister availability improves Titan's learning, but it is not required for
Titan to remain operational.

When Big Sister is offline and the eligible local routes have been exhausted,
Titan creates a sanitized lesson request containing:

- objective references;
- attempted routes;
- evidence references;
- required capabilities;
- the requested teaching outcome.

Raw prompts, credentials, secrets, tokens, authorization headers, and private
keys are forbidden from Step 31 evidence.

## Lesson packages

A lesson package must contain:

- governed instruction references;
- verification references;
- safety-policy references;
- version and creation information;
- optional examples and expiry information.

A lesson package is evidence. It does not independently authorize execution or
promotion into durable memory.

## Persistence and observability

Learning decisions may be stored in the checksummed append-only
`learning-hierarchy.jsonl` journal.

Mission Control records decisions through the
`learning_hierarchy_recorded` event type. Publication is idempotent.

## Governance invariants

1. Local capability is preferred over remote escalation.
2. Paid or cloud escalation never bypasses approval.
3. Big Sister teaches but does not silently take ownership of Titan.
4. Titan queues lessons instead of failing merely because Mac is offline.
5. Learning depth is bounded.
6. Decision evidence is immutable, deterministic, sanitized, and replayable.
7. Step 31 never increases execution authority.
