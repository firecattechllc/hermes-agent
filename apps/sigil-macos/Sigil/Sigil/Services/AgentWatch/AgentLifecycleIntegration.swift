import Foundation
/// Owns cleanup for the sanitized, local-only lifecycle evidence directory.
/// Native Claude Code and Codex hooks write allowlisted records here. Sigil
/// never reads transcripts, prompts, tool data, or agent-owned state.
@MainActor
final class AgentLifecycleIntegration {
    private let evidenceDirectory: URL

    nonisolated init(
        evidenceDirectory: URL = AgentWatchPaths.evidenceDirectory()
    ) {
        self.evidenceDirectory = evidenceDirectory
    }

    func start() { removeOwnedEvidence() }

    func stop() { removeOwnedEvidence() }

    private func removeOwnedEvidence() {
        guard let entries = try? FileManager.default.contentsOfDirectory(
            at: evidenceDirectory,
            includingPropertiesForKeys: nil
        ) else { return }
        for url in entries where Self.isOwnedEvidence(url.lastPathComponent) {
            try? FileManager.default.removeItem(at: url)
        }
    }

    nonisolated static func isOwnedEvidence(_ name: String) -> Bool {
        (name.hasPrefix("claudeCode-") || name.hasPrefix("codex-")) && name.hasSuffix(".json")
    }
}
