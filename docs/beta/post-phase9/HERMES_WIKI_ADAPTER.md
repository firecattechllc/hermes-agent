# Hermes Wiki Adapter

## Stage 7 status

Stage 7 defines a disabled-by-default governed Hermes Wiki knowledge adapter
over the Stage 1 integration registry and Stage 2 worker/job contract.

Hermes Wiki is modeled as an immutable, citation-backed knowledge surface for
runbooks, design documents, policies, references, incidents, and decisions.

No live Wiki service is crawled, authenticated, indexed, edited, or published
during Stage 7.

## Modeled concepts

The adapter models immutable references for:

- knowledge namespaces;
- document identity and type;
- document revisions;
- content digests;
- document links;
- citations;
- provenance;
- source references;
- tags;
- index state;
- embedding-model identity;
- indexed chunk count;
- retrieval evidence;
- retrieval ranking and score;
- freshness;
- worker-job correlation and idempotency;
- worker lifecycle projection.

## Document boundary

Each Wiki document includes:

- immutable document identity;
- namespace;
- document kind;
- current revision identity;
- revision content digest;
- revision provenance;
- links;
- citations;
- tags;
- canonical timestamps;
- deterministic document digest.

Documents are projections only. The adapter cannot edit or publish them.

## Revision boundary

Revision references require:

- immutable revision identity;
- SHA-256 content digest;
- canonical timestamp;
- author identity;
- repository-relative source reference.

The adapter rejects:

- private host paths;
- home-directory paths;
- parent traversal;
- private endpoints;
- credential material.

## Citation boundary

Citations require:

- immutable citation identity;
- source classification;
- source identity;
- content digest;
- provenance;
- repository-relative evidence reference.

Citation presence does not independently establish truth. It records the
evidence chain available to Hermes governance.

## Index semantics

Index evidence may report:

- `not_indexed`
- `pending`
- `indexed`
- `stale`
- `failed`
- `incompatible`

Indexed evidence requires at least one indexed chunk.

Stage 7 records index evidence but cannot:

- execute indexing;
- generate embeddings;
- select a model;
- modify an index;
- connect to a vector database;
- publish indexed content.

## Retrieval semantics

Retrieval evidence records:

- query digest;
- document and revision identity;
- retrieval timestamp;
- rank;
- bounded score;
- excerpt digest;
- provenance.

Retrieval status supports:

- `disabled`
- `available`
- `stale`
- `missing`
- `incompatible`
- `invalid`

The adapter fails closed for:

- missing index evidence;
- mismatched document identity;
- mismatched revision identity;
- stale evidence;
- future evidence;
- malformed provenance or references.

## Worker lifecycle projection

Stage 2 worker states project into descriptive Wiki work states:

- `proposed`
- `admitted`
- `rejected`
- `queued`
- `running`
- `cancellation_requested`
- `cancelled`
- `succeeded`
- `failed`
- `completion_unknown`

This projection does not execute retrieval or indexing.

## Registry relationship

Hermes Wiki must be represented by a Stage 1 registry entry with:

- matching integration identity;
- the `knowledge` category;
- an eligible lifecycle state;
- fully denied authority.

Rejected, deprecated, and quarantined entries fail closed.

## Authority boundary

The adapter cannot:

- crawl websites;
- connect to a live Wiki;
- authenticate;
- resolve credentials;
- read arbitrary files;
- write files;
- edit documents;
- publish documents;
- delete documents;
- execute indexing;
- generate embeddings;
- mutate a vector index;
- dispatch jobs;
- approve work;
- install or activate an integration;
- mutate policy;
- submit broker orders;
- mutate portfolio state;
- authorize or spend capital;
- bypass Hermes governance.

The inherited authority boundary remains:

- `paper_only = true`
- `broker_submission = false`
- `execution_authorized = false`
- `approval_authority = false`
- `capital_authority = false`
- `portfolio_mutation = false`
- `policy_mutation = false`
- `credential_access = false`
- `arbitrary_shell = false`
- `arbitrary_filesystem = false`
- `governance_bypass = false`
- `activation_authorized = false`
- `installation_authorized = false`

## Deferred work

Stage 7 does not include:

- Wiki installation;
- live service discovery;
- authentication;
- crawling;
- document ingestion;
- publishing;
- editing;
- deletion;
- live index generation;
- embedding generation;
- vector database access;
- live retrieval;
- Mission Control projection;
- ecosystem catalog integration;
- fleet routing.

Those remain later-stage work.
