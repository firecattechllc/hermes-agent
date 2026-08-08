import Foundation

/// `/governed_news_status` — the governed news evidence store's own
/// projection. `status` is "empty" until at least one item has been
/// ingested; that is a real, honest state, not a placeholder.
struct GovernedNewsStatus: Decodable {
    let status: String
    let headlineCount: Int
    let symbolCount: Int
    let lastCollectedAt: String?
    let headlines: [JSONRecord]

    enum CodingKeys: String, CodingKey {
        case status
        case headlineCount = "headline_count"
        case symbolCount = "symbol_count"
        case lastCollectedAt = "last_collected_at"
        case headlines
    }
}
