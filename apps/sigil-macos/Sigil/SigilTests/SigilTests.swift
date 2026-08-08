import Testing
@testable import SigilDev

/// Smoke test confirming the test target itself is wired up correctly
/// (correct module name, `@testable import` resolves). Substantive
/// coverage lives in the topic-specific files alongside this one:
/// `SafetyBoundaryTests`, `StatusSemanticsTests`, `JSONValueTests`,
/// `BridgeDecodingTests`, `KeychainStoreTests`, `StoreTests`.
struct SigilTests {
    @Test func testTargetLinksAgainstTheAppModule() {
        #expect(SafetyPosture.current.isPaperOnly == true)
    }
}
