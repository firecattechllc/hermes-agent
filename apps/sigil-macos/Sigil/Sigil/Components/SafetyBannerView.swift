import SwiftUI

/// Always-visible safety boundary banner: paper-only, no live execution
/// authority, broker submission disabled. This must never be hidden or
/// softened — it is the app's core governance guarantee.
struct SafetyBannerView: View {
    let safety: SafetyPosture

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "shield.lefthalf.filled")
                    .foregroundStyle(.orange)
                Text("Safety Boundaries")
                    .font(.headline)
            }

            boundaryRow(
                icon: safety.isPaperOnly ? "checkmark.seal.fill" : "exclamationmark.triangle.fill",
                tint: safety.isPaperOnly ? .green : .red,
                title: safety.isPaperOnly ? "Paper Only" : "Paper-Only Mode Off",
                detail: "This client operates in paper-only mode. No real capital is at risk from this app."
            )

            boundaryRow(
                icon: safety.hasLiveExecutionAuthority ? "exclamationmark.triangle.fill" : "lock.fill",
                tint: safety.hasLiveExecutionAuthority ? .red : .secondary,
                title: "No Live Execution Authority",
                detail: safety.authorityNote
            )

            boundaryRow(
                icon: safety.isBrokerSubmissionEnabled ? "exclamationmark.triangle.fill" : "lock.fill",
                tint: safety.isBrokerSubmissionEnabled ? .red : .secondary,
                title: safety.isBrokerSubmissionEnabled ? "Broker Submission Enabled" : "Broker Submission Disabled",
                detail: safety.brokerGovernanceNote
            )
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(Color.orange.opacity(0.25))
        )
    }

    private func boundaryRow(icon: String, tint: Color, title: String, detail: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .foregroundStyle(tint)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
