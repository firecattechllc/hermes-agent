# Sigil Step 6 — Governed Financial Document Ingestion

## Purpose

Create a deterministic, offline ingestion boundary that turns financial text into
traceable, evidence-ready document records without duplicating Hermes evidence storage.

## Contract

The ingestor:

- accepts bounded financial text from a governed upstream collector;
- validates issuer, document type, title, publication date, provenance, and metadata;
- requires absolute HTTPS provenance URLs and timezone-aware retrieval timestamps;
- normalizes incidental whitespace without summarizing or rewriting source meaning;
- computes SHA-256 content fingerprints;
- rejects duplicate content within the ingestion session;
- creates stable document and chunk identifiers;
- produces bounded overlapping chunks for retrieval and FinBERT analysis;
- emits a manifest suitable for Hermes evidence storage.

## Supported initial document types

- SEC 10-K
- SEC 10-Q
- SEC 8-K
- earnings-call transcript
- press release
- annual report
- investor presentation
- explicitly classified other financial material

## Non-goals

Step 6 does not:

- browse the web;
- call SEC or market-data APIs;
- parse PDF binary formats;
- perform OCR;
- summarize documents;
- execute trades;
- make investment recommendations;
- create an independent evidence database.

Collection, scheduling, secrets, durable evidence storage, approvals, and network policy
remain Hermes responsibilities.
