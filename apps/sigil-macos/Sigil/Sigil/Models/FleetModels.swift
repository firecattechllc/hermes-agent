import Foundation

/// A node in the Mac/Titan/Prime governed compute fleet.
///
/// Registration, health, and eligibility are kept as three separate signals
/// on purpose — a node can be registered but never connected (`.unknown`),
/// registered and degraded, or unregistered entirely, and those must never
/// collapse into a single badge. `capabilitiesText`/`modelInventoryText` are
/// plain informational strings (not states) for whatever the backend does
/// or doesn't expose. `modelInventoryText` lists models the node has
/// available — it is NOT "the model currently selected for routing"; no
/// backend field for that exists anywhere reachable from this client.
struct FleetNode: Identifiable, Hashable {
    let id = UUID()
    let name: String
    let role: String
    let registration: StatusEntry
    let health: StatusEntry
    let eligibility: StatusEntry
    let capabilitiesText: String
    let modelInventoryText: String
}
