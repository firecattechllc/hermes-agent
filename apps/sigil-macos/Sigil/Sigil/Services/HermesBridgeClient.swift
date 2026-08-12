import Foundation

/// Failure modes for a Hermes bridge request. Every case is a plain, honest
/// "could not get this value" — none of them are ever silently upgraded to
/// a successful-looking result.
enum HermesBridgeError: Error {
    case transport(Error)
    case httpStatus(Int)
    case decoding(Error)
    case backendRejected(error: String, message: String)
}

/// Thin HTTP client for Sigil's embedded XPC bridge. Talks only to
/// 127.0.0.1. Read methods issue GET against status/inspection routes.
/// The four `paperExecution*` methods issue POST against an explicit,
/// separately-allow-listed set of governed paper-automation lifecycle
/// commands — there is no generic "send any command" method anywhere here;
/// every reachable route is named one at a time, matching the bridge's own
/// explicit two-list design.
struct HermesBridgeClient {
    nonisolated static let defaultBaseURLString = "http://127.0.0.1:8799"

    private let baseURLString: String
    private let session: URLSession

    nonisolated init(baseURLString: String = HermesBridgeClient.defaultBaseURLString) {
        self.baseURLString = baseURLString
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 3
        configuration.timeoutIntervalForResource = 3
        configuration.waitsForConnectivity = false
        self.session = URLSession(configuration: configuration)
    }

    private func request<Result: Decodable>(_ method: String, _ route: String, as type: Result.Type) async throws -> Result {
        guard let url = URL(string: "\(baseURLString)\(route)") else {
            throw HermesBridgeError.transport(URLError(.badURL))
        }
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = method

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: urlRequest)
        } catch {
            throw HermesBridgeError.transport(error)
        }

        guard let httpResponse = response as? HTTPURLResponse else {
            throw HermesBridgeError.transport(URLError(.badServerResponse))
        }
        guard httpResponse.statusCode == 200 else {
            throw HermesBridgeError.httpStatus(httpResponse.statusCode)
        }

        let envelope: BridgeEnvelope<Result>
        do {
            envelope = try JSONDecoder().decode(BridgeEnvelope<Result>.self, from: data)
        } catch {
            throw HermesBridgeError.decoding(error)
        }

        guard envelope.ok, let result = envelope.result else {
            throw HermesBridgeError.backendRejected(
                error: envelope.error ?? "unknown_error",
                message: envelope.message ?? ""
            )
        }
        return result
    }

    // MARK: - Read (GET)

    func health() async throws -> BridgeHealthResult {
        try await request("GET", "/health", as: BridgeHealthResult.self)
    }

    func runtimeSnapshot() async throws -> RuntimeSnapshotResult {
        try await request("GET", "/runtime_snapshot", as: RuntimeSnapshotResult.self)
    }

    func aiStatus() async throws -> AIStatusResult {
        try await request("GET", "/ai_status", as: AIStatusResult.self)
    }

    func primeFleetStatus() async throws -> PrimeFleetStatusResult {
        try await request("GET", "/prime_fleet_status", as: PrimeFleetStatusResult.self)
    }

    func paperExecutionStatus() async throws -> PaperExecutionStatus {
        try await request("GET", "/paper_execution_status", as: PaperExecutionStatus.self)
    }

    func paperPositions() async throws -> PaperCollectionResult {
        try await request("GET", "/paper_positions", as: PaperCollectionResult.self)
    }

    func paperOrders() async throws -> PaperCollectionResult {
        try await request("GET", "/paper_orders", as: PaperCollectionResult.self)
    }

    func paperFills() async throws -> PaperCollectionResult {
        try await request("GET", "/paper_fills", as: PaperCollectionResult.self)
    }

    func recentProposals() async throws -> PaperCollectionResult {
        try await request("GET", "/recent_proposals", as: PaperCollectionResult.self)
    }

    func recentCandidates() async throws -> PaperCollectionResult {
        try await request("GET", "/recent_candidates", as: PaperCollectionResult.self)
    }

    func recentRejections() async throws -> PaperCollectionResult {
        try await request("GET", "/recent_rejections", as: PaperCollectionResult.self)
    }

    func recentAudit() async throws -> PaperCollectionResult {
        try await request("GET", "/recent_audit", as: PaperCollectionResult.self)
    }

    func governedNewsStatus() async throws -> GovernedNewsStatus {
        try await request("GET", "/governed_news_status", as: GovernedNewsStatus.self)
    }

    // MARK: - Governed paper-automation lifecycle (POST)
    //
    // Every one of these is paper-only and already exists, already audited,
    // in the backend's own command allow-list; this client adds no new
    // backend capability, it only reaches four already-governed actions.

    func paperExecutionActivate() async throws -> PaperExecutionStatus {
        try await request("POST", "/paper_execution_activate", as: PaperExecutionStatus.self)
    }

    func paperExecutionDeactivate() async throws -> PaperExecutionStatus {
        try await request("POST", "/paper_execution_deactivate", as: PaperExecutionStatus.self)
    }

    func paperExecutionPause() async throws -> PaperExecutionStatus {
        try await request("POST", "/paper_execution_pause", as: PaperExecutionStatus.self)
    }

    func paperExecutionResume() async throws -> PaperExecutionStatus {
        try await request("POST", "/paper_execution_resume", as: PaperExecutionStatus.self)
    }
}
