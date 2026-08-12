import Foundation
import Network

@objc protocol HermesBridgeServiceProtocol {
    func start(
        apiKey: String?,
        secretKey: String?,
        primeBaseURL: String?,
        primeAuthToken: String?,
        reply: @escaping (Bool, String?) -> Void
    )
    func stop(reply: @escaping () -> Void)
}

private let safety: [String: Any] = [
    "data_only": true,
    "execution_authorized": false,
    "broker_submission_available": false,
    "live_trading_enabled": false,
]

private final class LoopbackHTTPServer: @unchecked Sendable {
    private let queue = DispatchQueue(label: "com.firecattechnology.sigil.bridge.http")
    private var listener: NWListener?
    private var apiKey: String?
    private var secretKey: String?
    private var primeBaseURL: String?
    private var primeAuthToken: String?

    func start(
        apiKey: String?,
        secretKey: String?,
        primeBaseURL: String?,
        primeAuthToken: String?,
        completion: @escaping (Result<Void, Error>) -> Void
    ) {
        queue.async {
            guard self.listener == nil else {
                completion(.success(()))
                return
            }
            self.apiKey = apiKey
            self.secretKey = secretKey
            self.primeBaseURL = primeBaseURL
            self.primeAuthToken = primeAuthToken
            do {
                let parameters = NWParameters.tcp
                parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: 8799)
                let listener = try NWListener(using: parameters)
                var replied = false
                listener.stateUpdateHandler = { state in
                    guard !replied else { return }
                    switch state {
                    case .ready:
                        replied = true
                        completion(.success(()))
                    case .failed(let error):
                        replied = true
                        completion(.failure(error))
                    default: break
                    }
                }
                listener.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
                self.listener = listener
                listener.start(queue: self.queue)
            } catch {
                completion(.failure(error))
            }
        }
    }

    func stop() {
        queue.sync {
            listener?.cancel()
            listener = nil
            apiKey = nil
            secretKey = nil
            primeBaseURL = nil
            primeAuthToken = nil
        }
    }

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16_384) { [weak self] data, _, _, _ in
            guard let self, let data, data.count <= 16_384 else {
                connection.cancel()
                return
            }
            Task { await self.respond(to: data, on: connection) }
        }
    }

    private func respond(to requestData: Data, on connection: NWConnection) async {
        let request = String(decoding: requestData, as: UTF8.self)
        let firstLine = request.split(separator: "\r\n", maxSplits: 1).first?.split(separator: " ") ?? []
        guard firstLine.count >= 2 else { send(status: 400, object: failure("invalid_request"), on: connection); return }
        let method = String(firstLine[0])
        let route = String(firstLine[1]).split(separator: "?", maxSplits: 1).first.map(String.init) ?? ""

        if method == "GET", route == "/prime_fleet_status" {
            let result = await primeFleetStatus()
            send(status: 200, object: ["ok": true, "result": result], on: connection)
        } else if method == "GET" {
            guard let response = readResponse(route) else {
                send(status: 404, object: failure("not_found"), on: connection); return
            }
            send(status: 200, object: response, on: connection)
        } else if method == "POST", route == "/market_universe_quotes" {
            guard let separator = request.range(of: "\r\n\r\n") else {
                send(status: 400, object: failure("invalid_payload"), on: connection); return
            }
            let body = Data(request[separator.upperBound...].utf8)
            guard body.count <= 4_096,
                  let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                  let rawSymbols = object["symbols"] as? [String]
            else { send(status: 400, object: failure("invalid_payload"), on: connection); return }
            let result = await marketQuotes(rawSymbols)
            send(status: 200, object: ["ok": true, "result": result], on: connection)
        } else if method == "POST", lifecycleRoutes.contains(route) {
            guard validEmptyLifecyclePayload(request) else {
                send(status: 400, object: failure("invalid_payload"), on: connection); return
            }
            send(status: 200, object: lifecycleUnavailableResponse(route), on: connection)
        } else {
            send(status: 404, object: failure("not_found"), on: connection)
        }
    }

    private let lifecycleRoutes: Set<String> = [
        "/paper_execution_activate",
        "/paper_execution_pause",
        "/paper_execution_resume",
        "/paper_execution_deactivate",
    ]

    private func validEmptyLifecyclePayload(_ request: String) -> Bool {
        guard let separator = request.range(of: "\r\n\r\n") else { return true }
        let body = request[separator.upperBound...].trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return true }
        guard body.utf8.count <= 128,
              let data = body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return false }
        return object.isEmpty
    }

    private func lifecycleUnavailableResponse(_ route: String) -> [String: Any] {
        let action = route.replacingOccurrences(of: "/paper_execution_", with: "")
        return [
            "ok": false,
            "error": "governed_backend_not_configured",
            "message": "Cannot \(action): the embedded bridge is connected, but a governed paper-runtime backend is not configured for this Mac.",
        ]
    }

    private func readResponse(_ route: String) -> [String: Any]? {
        let result: [String: Any]
        switch route {
        case "/health":
            result = ["bridge_version": "4.0.0", "status": "healthy", "mode": "embedded_xpc", "safety": safety]
        case "/runtime_snapshot":
            result = ["runtime_visibility": ["operational_state": "stopped", "health": "not_configured", "connection_state": "not_configured", "automation_mode": "paper_only", "paper_execution_available": false, "counts": ["cycles": 0, "proposals": 0, "executions": 0, "reconciliation": 0, "audit_events": 0]], "safety": safety]
        case "/ai_status":
            result = ["enabled": false, "service_state": "disabled", "local_gemma_health": "disabled", "configured_model_count": 0, "available_model_count": 0, "registry_revision": "unconfigured", "mac_ollama": ["enabled": false, "roles": [:]], "evidence_record_count": 0, "artifact_count": 0, "evidence_ledger_health": "empty", "artifact_store_health": "empty", "latest_analysis_summary": NSNull(), "orchestration": ["enabled": false, "health": "disabled", "active_count": 0, "completed_count": 0, "failed_count": 0, "paused_count": 0], "fleet": ["registered_node_count": 0, "healthy_node_count": 0, "nodes": ["titan": NSNull(), "mac": NSNull(), "prime": NSNull()], "latest_route": NSNull(), "latest_failover": NSNull(), "active_tasks": 0, "queued_tasks": 0, "completion_unknown_tasks": 0, "clock_warnings": 0], "safety": safety]
        case "/paper_execution_status":
            result = ["environment": "paper", "live_execution": false, "broker": "none", "broker_submission": false, "lifecycle_actions_available": false, "lifecycle_unavailable_reason": "Governed paper-runtime backend not configured", "activated": false, "paused": false, "kill_switch": true, "degraded_conditions": [], "unmanaged_position_symbols": [], "open_positions": 0, "open_orders": 0, "deployed_paper_capital": "0", "remaining_governed_allocation": "0", "last_reconciliation": NSNull(), "last_order_intent": NSNull(), "last_submitted_order": NSNull(), "last_fill": NSNull(), "last_rejection": NSNull(), "safety": safety]
        case "/paper_positions", "/paper_orders", "/paper_fills", "/recent_proposals", "/recent_candidates", "/recent_rejections", "/recent_audit":
            result = ["broker_submission": false, "revision": 0, "offset": 0, "limit": 50, "total": 0, "has_more": false, "items": [], "safety": safety]
        case "/governed_news_status":
            result = ["status": "empty", "headline_count": 0, "symbol_count": 0, "last_collected_at": NSNull(), "headlines": [], "safety": safety]
        default:
            return nil
        }
        return ["ok": true, "result": result]
    }

    /// Real, governed visibility into the Hermes Prime fleet control plane —
    /// the native-Swift counterpart to
    /// `apps/sigil/src/sigil/desktop_bridge/prime_fleet.py`'s
    /// `prime_fleet_status()`. Same contract (`GET /v1/fleet/nodes`,
    /// `GET /v1/fleet/certification`, `Authorization: Bearer <token>`), same
    /// fail-closed/honest shape: not configured, unreachable, and malformed
    /// responses are all reported as such — never a fabricated healthy fleet.
    private func primeFleetStatus() async -> [String: Any] {
        guard let primeBaseURL, !primeBaseURL.isEmpty, let primeAuthToken, !primeAuthToken.isEmpty else {
            return ["configured": false, "reachable": false, "base_url": NSNull(), "nodes": [], "certification": ["status": "unknown", "evidence_ref": NSNull()], "safety": safety]
        }
        let base = primeBaseURL.hasSuffix("/") ? String(primeBaseURL.dropLast()) : primeBaseURL

        async let nodesCall = primeRequest(base: base, token: primeAuthToken, path: "/v1/fleet/nodes")
        async let certCall = primeRequest(base: base, token: primeAuthToken, path: "/v1/fleet/certification")
        let (nodesStatus, nodesBody) = await nodesCall
        let (certStatus, certBody) = await certCall

        let reachable = nodesStatus == 200 && certStatus == 200
        let nodes = reachable ? (nodesBody?["nodes"] as? [[String: Any]] ?? []) : []
        let certification = reachable ? (certBody ?? ["status": "unknown", "evidence_ref": NSNull()]) : ["status": "unknown", "evidence_ref": NSNull()]

        return ["configured": true, "reachable": reachable, "base_url": base, "nodes": nodes, "certification": certification, "safety": safety]
    }

    /// Returns `(status_code, parsed_body)`. `status_code` is `nil` only for
    /// a network-level failure (unreachable, timeout, DNS) — mirrors
    /// `prime_fleet._request`'s contract exactly.
    private func primeRequest(base: String, token: String, path: String) async -> (Int?, [String: Any]?) {
        guard let url = URL(string: "\(base)\(path)") else { return (nil, nil) }
        var request = URLRequest(url: url)
        request.timeoutInterval = 8
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard data.count <= 1_048_576, let httpResponse = response as? HTTPURLResponse else { return (nil, nil) }
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            return (httpResponse.statusCode, object)
        } catch {
            return (nil, nil)
        }
    }

    private func marketQuotes(_ requested: [String]) async -> [String: Any] {
        let pattern = try! NSRegularExpression(pattern: "^[A-Z0-9.-]{1,16}$")
        var symbols: [String] = []
        for raw in requested {
            let symbol = raw.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
            let range = NSRange(symbol.startIndex..., in: symbol)
            guard pattern.firstMatch(in: symbol, range: range) != nil else { continue }
            if !symbols.contains(symbol) { symbols.append(symbol) }
        }
        guard (1...20).contains(symbols.count), let apiKey, let secretKey else {
            return unavailableQuotes(symbols, reason: symbols.isEmpty ? "invalid_symbols" : "credentials_unavailable")
        }
        var components = URLComponents(string: "https://data.alpaca.markets/v2/stocks/snapshots")!
        components.queryItems = [.init(name: "symbols", value: symbols.joined(separator: ",")), .init(name: "feed", value: "iex")]
        var request = URLRequest(url: components.url!)
        request.timeoutInterval = 5
        request.setValue(apiKey, forHTTPHeaderField: "APCA-API-KEY-ID")
        request.setValue(secretKey, forHTTPHeaderField: "APCA-API-SECRET-KEY")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard data.count <= 1_048_576, (response as? HTTPURLResponse)?.statusCode == 200,
                  let snapshots = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { return unavailableQuotes(symbols, reason: "provider_unavailable") }
            let quotes = symbols.map { symbol -> [String: Any] in
                let snapshot = snapshots[symbol] as? [String: Any]
                let trade = snapshot?["latestTrade"] as? [String: Any]
                let daily = snapshot?["dailyBar"] as? [String: Any]
                let previous = snapshot?["prevDailyBar"] as? [String: Any]
                let price = trade?["p"] ?? daily?["c"] ?? NSNull()
                return ["symbol": symbol, "price": price, "previous_close": previous?["c"] ?? NSNull(), "observed_at": trade?["t"] ?? daily?["t"] ?? NSNull(), "freshness": price is NSNull ? "unavailable" : "iex", "source": price is NSNull ? "Price unavailable" : "IEX", "reason": price is NSNull ? "snapshot_missing" : NSNull()]
            }
            return ["feed": "iex", "quotes": quotes, "data_only": true, "execution_authorized": false, "broker_submission_available": false, "live_trading_enabled": false]
        } catch {
            return unavailableQuotes(symbols, reason: "provider_unavailable")
        }
    }

    private func unavailableQuotes(_ symbols: [String], reason: String) -> [String: Any] {
        ["feed": "unavailable", "quotes": symbols.map { ["symbol": $0, "price": NSNull(), "freshness": "unavailable", "source": "Price unavailable", "reason": reason] }, "data_only": true, "execution_authorized": false, "broker_submission_available": false, "live_trading_enabled": false]
    }

    private func failure(_ error: String) -> [String: Any] { ["ok": false, "error": error, "message": error] }

    private func send(status: Int, object: [String: Any], on connection: NWConnection) {
        let body = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{\"ok\":false}".utf8)
        let reason = status == 200 ? "OK" : status == 404 ? "Not Found" : "Bad Request"
        var response = Data("HTTP/1.1 \(status) \(reason)\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n".utf8)
        response.append(body)
        connection.send(content: response, completion: .contentProcessed { _ in connection.cancel() })
    }
}

private final class Service: NSObject, HermesBridgeServiceProtocol {
    private let server = LoopbackHTTPServer()
    func start(
        apiKey: String?,
        secretKey: String?,
        primeBaseURL: String?,
        primeAuthToken: String?,
        reply: @escaping (Bool, String?) -> Void
    ) {
        server.start(
            apiKey: apiKey,
            secretKey: secretKey,
            primeBaseURL: primeBaseURL,
            primeAuthToken: primeAuthToken
        ) { result in
            switch result { case .success: reply(true, nil); case .failure(let error): reply(false, error.localizedDescription) }
        }
    }
    func stop(reply: @escaping () -> Void) { server.stop(); reply() }
}

private final class Delegate: NSObject, NSXPCListenerDelegate {
    private let service = Service()
    func listener(_ listener: NSXPCListener, shouldAcceptNewConnection connection: NSXPCConnection) -> Bool {
        connection.exportedInterface = NSXPCInterface(with: HermesBridgeServiceProtocol.self)
        connection.exportedObject = service
        connection.resume()
        return true
    }
}

private let delegate = Delegate()
private let listener = NSXPCListener.service()
listener.delegate = delegate
listener.resume()
