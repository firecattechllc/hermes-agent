import Foundation
import OSLog

/// Consumes a loopback-only, already-sanitized Codex lifecycle source.
/// The source runs outside App Sandbox and owns the documented app-server
/// connection; Sigil never executes Codex or accesses Codex-owned files.
@MainActor
final class AgentLifecycleIntegration {
    private let logger = Logger(subsystem: "com.firecattechnology.Sigil.dev", category: "AgentWatchLifecycle")
    private let endpoint: URL
    private let evidenceDirectory: URL
    private var pollingTask: Task<Void, Never>?
    private var lastCodexEvidenceURL: URL?

    nonisolated init(
        endpoint: URL = URL(string: "http://127.0.0.1:47841/v1/evidence")!,
        evidenceDirectory: URL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appending(path: "SigilDev/AgentWatch/Events", directoryHint: .isDirectory)
    ) {
        self.endpoint = endpoint
        self.evidenceDirectory = evidenceDirectory
    }

    func start() {
        guard pollingTask == nil else { return }
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func stop() {
        pollingTask?.cancel()
        pollingTask = nil
        removeCodexEvidence()
    }

    private func refresh() async {
        do {
            let (data, response) = try await URLSession.shared.data(from: endpoint)
            guard let http = response as? HTTPURLResponse else {
                removeCodexEvidence()
                return
            }
            guard http.statusCode == 200 else {
                removeCodexEvidence()
                return
            }
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  Set(object.keys) == ["agent", "processID", "event", "observedAt"],
                  let evidence = try? JSONDecoder.agentWatch.decode(SanitizedAgentEvent.self, from: data),
                  evidence.agent == .codex
            else {
                removeCodexEvidence()
                return
            }
            try FileManager.default.createDirectory(at: evidenceDirectory, withIntermediateDirectories: true)
            let target = evidenceDirectory.appending(path: "codex-\(evidence.processID).json")
            try data.write(to: target, options: [.atomic, .completeFileProtection])
            if lastCodexEvidenceURL != target { removeCodexEvidence(except: target) }
            lastCodexEvidenceURL = target
        } catch {
            removeCodexEvidence()
        }
    }

    private func removeCodexEvidence(except retainedURL: URL? = nil) {
        if let url = lastCodexEvidenceURL, url != retainedURL {
            try? FileManager.default.removeItem(at: url)
            lastCodexEvidenceURL = nil
            logger.notice("Codex lifecycle source unavailable; evidence cleared")
        }
    }

    deinit { pollingTask?.cancel() }
}
