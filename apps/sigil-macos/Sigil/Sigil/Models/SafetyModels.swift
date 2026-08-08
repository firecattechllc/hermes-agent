import Foundation

/// Governance/safety posture of the runtime. Sigil 4.0's native shell is a
/// thin client — this struct exists so those boundaries are always visible
/// in the UI and never silently assumed away.
struct SafetyPosture: Hashable {
    var isPaperOnly: Bool
    var hasLiveExecutionAuthority: Bool
    var isBrokerSubmissionEnabled: Bool
    var brokerGovernanceNote: String
    var authorityNote: String

    static let current = SafetyPosture(
        isPaperOnly: true,
        hasLiveExecutionAuthority: false,
        isBrokerSubmissionEnabled: false,
        brokerGovernanceNote: "Broker submission is disabled unless explicitly governed by the existing paper runtime.",
        authorityNote: "Hermes remains authoritative. This client cannot execute, override, or bypass governed decisions."
    )
}
