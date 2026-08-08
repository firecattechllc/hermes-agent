import Testing
@testable import SigilDev

@MainActor
struct StoreTests {
    @Test func missionControlStoreSeedsWithLoadingStateNotBlank() {
        let store = MissionControlStore(provider: MockMissionControlDataProvider())
        // The first frame must never show an empty/uninitialized snapshot —
        // every field is seeded synchronously before any network call.
        #expect(store.snapshot.runtimeHealth.detail == "Loading…")
        #expect(store.snapshot.fleetNodes.isEmpty)
        #expect(store.lastUpdatedAt == nil)
    }

    @Test func missionControlStoreRefreshPopulatesFromProvider() async {
        let store = MissionControlStore(provider: MockMissionControlDataProvider())
        await store.refresh()

        #expect(store.lastUpdatedAt != nil)
        #expect(store.isLoading == false)
        // Mock provider always returns .mockData — refresh must not silently
        // upgrade that to a connected/healthy state.
        #expect(store.snapshot.runtimeHealth.state == .mockData)
    }

    @Test func mockProviderReturnsThreeFleetNodesNamedTitanMacPrime() async {
        let snapshot = await MockMissionControlDataProvider().fetchSnapshot()
        let names = Set(snapshot.fleetNodes.map(\.name))
        #expect(names == ["Titan", "Mac", "Prime"])
    }

    @Test func paperExecutionStoreStartsWithNoStatusUntilRefreshed() {
        let store = PaperExecutionStore()
        #expect(store.status == nil)
        #expect(store.isLoading == false)
        #expect(store.isPerformingAction == false)
    }

    @Test func paperCollectionStoreStartsEmptyUntilRefreshed() {
        let store = PaperCollectionStore(fetch: { try await HermesBridgeClient().paperOrders() })
        #expect(store.result == nil)
        #expect(store.errorMessage == nil)
    }
}
