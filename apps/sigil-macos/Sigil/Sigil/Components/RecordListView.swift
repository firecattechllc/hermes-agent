import SwiftUI

/// Renders a list of backend records (paper orders, fills, proposals, audit
/// entries, ...) generically. Prefers a few common, human-meaningful keys
/// for the row's title/subtitle if present, and always shows the full
/// remaining fields underneath — nothing is hidden or reformatted away.
struct RecordListView: View {
    let records: [JSONRecord]
    let emptyMessage: String

    var body: some View {
        if records.isEmpty {
            Text(emptyMessage)
                .font(.callout)
                .foregroundStyle(.secondary)
                .padding(.vertical, 8)
        } else {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(records.enumerated()), id: \.offset) { index, record in
                    RecordRowView(record: record)
                    if index < records.count - 1 {
                        Divider()
                    }
                }
            }
        }
    }
}

private struct RecordRowView: View {
    let record: JSONRecord

    private static let titleKeys = ["symbol", "proposal_id", "audit_id", "order_id", "event"]
    private static let subtitleKeys = ["side", "status", "state", "event", "timestamp", "created_at"]

    private var title: String {
        for key in Self.titleKeys {
            if case .string(let value)? = record[key] { return value }
        }
        return record.keys.sorted().first.map { "\($0): \(record[$0]?.displayString ?? "—")" } ?? "Record"
    }

    private var subtitleParts: [String] {
        Self.subtitleKeys.compactMap { key in
            guard let value = record[key] else { return nil }
            return "\(key): \(value.displayString)"
        }
    }

    private var remainingFields: [(String, JSONValue)] {
        let shown = Set(Self.titleKeys + Self.subtitleKeys)
        return record
            .filter { !shown.contains($0.key) }
            .sorted { $0.key < $1.key }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.body.weight(.medium))
            if !subtitleParts.isEmpty {
                Text(subtitleParts.joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if !remainingFields.isEmpty {
                Text(remainingFields.map { "\($0.0): \($0.1.displayString)" }.joined(separator: " · "))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
