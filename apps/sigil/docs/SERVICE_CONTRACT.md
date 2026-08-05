# Initial Hermes Service Contract

Sigil supplies a normalized company identity and research questions.
Hermes returns graph context, memory context, a governed analysis result, and durable evidence IDs.

The contract is deliberately transport-neutral. HTTP, local Python, queues, or Mac↔Titan routing
may implement it without changing Sigil's domain workflow.
