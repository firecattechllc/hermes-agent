import SwiftUI

/// Card for a single fleet node (Mac / Titan / Prime). Registration, health,
/// and eligibility are shown as three separate rows — never one collapsed
/// badge — since a node can be, for example, registered but never connected.
struct FleetCardView: View {
    let node: FleetNode

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(node.name)
                    .font(.subheadline.weight(.semibold))
                Spacer()
            }
            Text(node.role)
                .font(.caption)
                .foregroundStyle(.secondary)

            VStack(alignment: .leading, spacing: 6) {
                fleetSignalRow(node.registration)
                fleetSignalRow(node.health)
                fleetSignalRow(node.eligibility)
            }
            .padding(.top, 2)

            Divider()

            VStack(alignment: .leading, spacing: 2) {
                Text("Capabilities: \(node.capabilitiesText)")
                Text("Model inventory: \(node.modelInventoryText)")
            }
            .font(.caption2)
            .foregroundStyle(.tertiary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.tertiary, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private func fleetSignalRow(_ entry: StatusEntry) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text(entry.title + ":")
                .font(.caption.weight(.medium))
            Text(entry.detail)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer(minLength: 4)
            StatusBadge(state: entry.state)
        }
    }
}
