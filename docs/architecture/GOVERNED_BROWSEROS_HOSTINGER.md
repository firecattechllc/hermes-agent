# Governed BrowserOS capability on Hostinger

Hermes treats browser automation as an optional external capability. The
`BrowserCapability` protocol separates availability, health, tool discovery,
and explicit execution. `BrowserOSMCPAdapter` is the sole provider adapter in
this phase and accepts only loopback Streamable HTTP MCP. BrowserOS itself binds
its internal proxy/backend on all container interfaces, so a private Docker
bridge contains those sockets and only `127.0.0.1:9239` is published on the host.

The feature defaults off. It does not participate in Runway authorization,
Prime/Titan dispatch, financial execution, or broker submission. It never falls
back to Playwright, Chrome, or another browser. Tool names must first be returned
by MCP discovery, all operations are bounded, and failures are typed and closed.

Execution telemetry contains only timestamp, project/task identity, tool name,
safely parsed hostname, result status, duration, and error category. Arguments,
page content, cookies, credentials, authorization headers, profile data, and form
values are excluded. The BrowserOS profile belongs to a dedicated service user
and must be protected as credential-bearing state.

BrowserOS is AGPL-3.0. It remains separately installed and managed, with no
copied code or linked library. Legal review should confirm obligations for the
chosen deployment/distribution model before production activation.
