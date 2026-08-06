# Alexa+ Integration — Research Findings

**Status: research only, no code shipped this run.** See rationale below.

## Official supported integration path (confirmed)

A real, documented, currently-supported path exists:

- **Alexa Skills Kit (ASK)** — Custom Skill or Smart Home Skill, built against
  Amazon's documented JSON request/response contract
  (`LaunchRequest`/`IntentRequest`/`SessionEndedRequest` in, a `Response`
  object with `outputSpeech`/`card`/`shouldEndSession` out), delivered over
  HTTPS or AWS Lambda.
- Security requirement: every inbound request must be verified against
  Amazon's `SignatureCertChainUrl` + `Signature` headers (X.509 certificate
  chain validation) and a request-timestamp tolerance check, per Amazon's
  published skill security requirements.
- Account linking uses standard OAuth 2.0 (authorization-code grant),
  configured in the Alexa Developer Console.

This is real, current, and does not require bypassing any account
authentication or device security — it is the intended integration
surface.

## Why nothing was implemented this run

1. **No Amazon Developer account or credentials exist for this project.**
   Per explicit instruction, none were fabricated. Without a registered
   skill ID, there is no live endpoint to test a handler against, and no
   way to obtain Amazon's real certificate chain to validate signature
   verification against.
2. **This codebase's platform adapter interface
   (`gateway/platforms/base.py::BasePlatformAdapter`, 2300+ lines) is
   deeply tied to persistent-connection, session, and async-delivery
   semantics** built for chat platforms (Telegram, Discord, Slack, ...).
   Alexa's model is stateless request/response (closer to this codebase's
   own API-server adapter pattern, per `supports_async_delivery`'s
   documented stateless-adapter carve-out). Building a genuinely
   conformant adapter requires deciding how it fits that interface
   correctly, not just writing JSON parsing code.
3. **Untestable security-critical code is worse than no code.** The one
   part of an Alexa skill backend that actually matters for safety is
   request signature verification. Writing that logic without any way to
   validate it against Amazon's real certificate infrastructure would
   produce code that looks real but has never been proven correct --
   exactly the kind of "not a placeholder, but also not verified" result
   this run's own standards reject.

## What would unblock this

An Amazon Developer account with a registered skill (even in development
mode, which Amazon provides free) would let a future implementation:
build the adapter, register a Lambda/HTTPS endpoint, and validate signature
verification against Amazon's real cert chain end-to-end. Recommended
next step is provisioning that account, not writing more code first.
