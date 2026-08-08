import SwiftUI

/// Mission Control's Overview: safety boundaries, runtime health, and the
/// AI Foundation status board (Hermes, local model, fleet, registry,
/// routing, evidence). Fields backed by the real governed Hermes backend
/// are labeled with their own semantic health state (never green merely
/// because a request succeeded); anything the backend doesn't expose shows
/// "Unavailable" rather than a fabricated healthy value.
struct OverviewView: View {
    @ObservedObject var store: MissionControlStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header

                SafetyBannerView(safety: store.snapshot.safety)

                SectionCard(title: "Hermes Bridge", systemImage: "antenna.radiowaves.left.and.right") {
                    StatusRowView(entry: store.snapshot.bridgeConnectivity)
                }

                SectionCard(title: "Runtime Health", systemImage: "heart.text.square") {
                    VStack(alignment: .leading, spacing: 0) {
                        StatusRowView(entry: store.snapshot.runtimeHealth)
                        Divider()
                        StatusRowView(entry: store.snapshot.connectionState)
                        Divider()
                        StatusRowView(entry: store.snapshot.paperRuntimeState)
                        Divider()
                        StatusRowView(entry: store.snapshot.automationState)
                    }
                }

                aiFoundationSection
            }
            .padding(20)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task { await store.refresh() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(store.isLoading)
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text("Sigil 4.0")
                    .font(.largeTitle.bold())
                Spacer()
                if let lastUpdatedAt = store.lastUpdatedAt {
                    Text("Updated \(lastUpdatedAt.formatted(date: .omitted, time: .standard))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Text("Native Mission Control · Thin client over the governed Hermes backend")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private var aiFoundationSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("AI Foundation")
                .font(.title2.weight(.semibold))

            SectionCard(title: "Orchestration", systemImage: "point.3.connected.trianglepath.dotted") {
                VStack(alignment: .leading, spacing: 0) {
                    StatusRowView(entry: store.snapshot.hermesOrchestration)
                    Divider()
                    StatusRowView(entry: store.snapshot.modelRegistry)
                }
            }

            SectionCard(title: "Local AI (Mac Ollama)", systemImage: "cpu") {
                VStack(alignment: .leading, spacing: 0) {
                    StatusRowView(entry: store.snapshot.ollamaService)
                    Divider()
                    StatusRowView(entry: store.snapshot.installedModels)
                    Divider()
                    StatusRowView(entry: store.snapshot.loadedModels)
                    Divider()
                    StatusRowView(entry: store.snapshot.embeddingModelAvailability)
                    Divider()
                    StatusRowView(entry: store.snapshot.providerHealth)
                }
            }

            SectionCard(title: "Fleet", systemImage: "server.rack") {
                VStack(alignment: .leading, spacing: 12) {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: 12)], spacing: 12) {
                        ForEach(store.snapshot.fleetNodes) { node in
                            FleetCardView(node: node)
                        }
                    }
                    Divider()
                    StatusRowView(entry: store.snapshot.fleetCertification)
                    Divider()
                    StatusRowView(entry: store.snapshot.routing)
                }
            }

            SectionCard(title: "Evidence & Artifacts", systemImage: "archivebox") {
                VStack(alignment: .leading, spacing: 0) {
                    StatusRowView(entry: store.snapshot.evidence)
                    Divider()
                    StatusRowView(entry: store.snapshot.latestResult)
                }
            }
        }
    }
}

#Preview {
    OverviewView(store: MissionControlStore(provider: MockMissionControlDataProvider()))
}
