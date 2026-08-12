import Foundation

/// `/paper_execution_status` — the single governed authority for paper
/// automation lifecycle state. Backs Portfolio, Executions, Reconciliation,
/// and Launch, each reading only the fields relevant to it.
struct PaperExecutionStatus: Decodable {
    let environment: String
    let liveExecution: Bool
    let broker: String
    let brokerSubmission: Bool
    let lifecycleActionsAvailable: Bool?
    let lifecycleUnavailableReason: String?
    let activated: Bool
    let paused: Bool
    let killSwitch: Bool
    let degradedConditions: [String]
    let unmanagedPositionSymbols: [String]
    let openPositions: Int
    let openOrders: Int
    let deployedPaperCapital: String
    let remainingGovernedAllocation: String
    let lastReconciliation: String?
    let lastOrderIntent: JSONRecord?
    let lastSubmittedOrder: JSONRecord?
    let lastFill: JSONRecord?
    let lastRejection: JSONRecord?
    /// Present only in the response to `paperExecutionFlattenPositions()` —
    /// per-symbol close results plus `fully_flattened`, computed from a
    /// fresh post-close broker read, never from the close calls alone.
    let flattenResult: JSONRecord?

    enum CodingKeys: String, CodingKey {
        case environment
        case liveExecution = "live_execution"
        case broker
        case brokerSubmission = "broker_submission"
        case lifecycleActionsAvailable = "lifecycle_actions_available"
        case lifecycleUnavailableReason = "lifecycle_unavailable_reason"
        case activated, paused
        case killSwitch = "kill_switch"
        case degradedConditions = "degraded_conditions"
        case unmanagedPositionSymbols = "unmanaged_position_symbols"
        case openPositions = "open_positions"
        case openOrders = "open_orders"
        case deployedPaperCapital = "deployed_paper_capital"
        case remainingGovernedAllocation = "remaining_governed_allocation"
        case lastReconciliation = "last_reconciliation"
        case lastOrderIntent = "last_order_intent"
        case lastSubmittedOrder = "last_submitted_order"
        case lastFill = "last_fill"
        case lastRejection = "last_rejection"
        case flattenResult = "flatten_result"
    }
}

/// Shape shared by every `recent_*`/`paper_*` collection command
/// (`paper_positions`, `paper_orders`, `paper_fills`, `recent_proposals`,
/// `recent_candidates`, `recent_rejections`, `recent_audit`).
struct PaperCollectionResult: Decodable {
    let brokerSubmission: Bool
    let total: Int
    let hasMore: Bool
    let items: [JSONRecord]

    enum CodingKeys: String, CodingKey {
        case brokerSubmission = "broker_submission"
        case total
        case hasMore = "has_more"
        case items
    }
}
