import SwiftUI

/// Real, read-only governed proposal data — proposals awaiting/having
/// undergone governed review, the candidates they were ranked from, and
/// rejections with their reasons. Nothing here can submit or approve
/// anything; it is a read of what the governed backend already decided.
struct ProposalsView: View {
    @StateObject private var proposalsStore = PaperCollectionStore(fetch: { try await HermesBridgeClient().recentProposals() })
    @StateObject private var candidatesStore = PaperCollectionStore(fetch: { try await HermesBridgeClient().recentCandidates() })
    @StateObject private var rejectionsStore = PaperCollectionStore(fetch: { try await HermesBridgeClient().recentRejections() })

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                collectionCard(title: "Proposals", systemImage: "doc.text.magnifyingglass", store: proposalsStore, emptyMessage: "No governed proposals recorded yet.")
                collectionCard(title: "Candidates", systemImage: "list.number", store: candidatesStore, emptyMessage: "No research candidates recorded yet.")
                collectionCard(title: "Rejections", systemImage: "xmark.circle", store: rejectionsStore, emptyMessage: "No rejections recorded yet.")
            }
            .padding(20)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task {
                        await proposalsStore.refresh()
                        await candidatesStore.refresh()
                        await rejectionsStore.refresh()
                    }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
            }
        }
        .task {
            await proposalsStore.refresh()
            await candidatesStore.refresh()
            await rejectionsStore.refresh()
        }
    }

    @ViewBuilder
    private func collectionCard(title: String, systemImage: String, store: PaperCollectionStore, emptyMessage: String) -> some View {
        SectionCard(title: title, systemImage: systemImage) {
            if let error = store.errorMessage {
                StatusRowView(entry: StatusEntry(title: title, state: .offline, detail: error))
            } else if let result = store.result {
                RecordListView(records: result.items, emptyMessage: emptyMessage)
            } else {
                ProgressView().padding()
            }
        }
    }
}
