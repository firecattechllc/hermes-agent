import Foundation

/// A loosely-typed JSON value for backend record shapes (paper orders,
/// fills, proposals, audit entries, ...) that vary by record type. Using
/// this instead of a bespoke Codable struct per record avoids guessing at
/// or duplicating the backend's own schema in Swift — every key present in
/// the real response is preserved and can be displayed, and nothing is
/// fabricated for keys that aren't there.
enum JSONValue: Decodable, Hashable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            self = .null
        }
    }

    /// Compact single-line rendering suitable for a list row.
    var displayString: String {
        switch self {
        case .string(let value): return value
        case .number(let value):
            return value.truncatingRemainder(dividingBy: 1) == 0 ? String(Int(value)) : String(value)
        case .bool(let value): return value ? "true" : "false"
        case .null: return "—"
        case .array(let values): return "[\(values.count) items]"
        case .object: return "{…}"
        }
    }
}

/// One backend record (a paper order, fill, proposal, audit entry, ...).
typealias JSONRecord = [String: JSONValue]
