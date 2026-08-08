import SwiftUI

/// A single row pairing a status title/detail with its `StatusBadge`.
struct StatusRowView: View {
    let entry: StatusEntry

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.title)
                    .font(.body.weight(.medium))
                Text(entry.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            StatusBadge(state: entry.state)
        }
        .padding(.vertical, 4)
    }
}
