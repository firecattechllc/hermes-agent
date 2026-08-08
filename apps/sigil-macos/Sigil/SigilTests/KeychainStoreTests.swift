import Foundation
import Testing
@testable import SigilDev

/// `KeychainStore` is the only place this app ever persists a credential
/// (Settings' Alpaca paper-trading fields) — these tests confirm the
/// round-trip works and that clearing actually removes the item, not just
/// blanks it.
struct KeychainStoreTests {
    private let testAccount = "sigil_dev_test_account_\(UUID().uuidString)"

    @Test func setThenGetReturnsTheStoredValue() {
        KeychainStore.set("test-value-123", forAccount: testAccount)
        defer { KeychainStore.delete(forAccount: testAccount) }

        #expect(KeychainStore.get(forAccount: testAccount) == "test-value-123")
    }

    @Test func getReturnsNilForAnAccountThatWasNeverSet() {
        #expect(KeychainStore.get(forAccount: "sigil_dev_test_never_set_\(UUID().uuidString)") == nil)
    }

    @Test func deleteRemovesTheStoredValue() {
        KeychainStore.set("to-be-deleted", forAccount: testAccount)
        KeychainStore.delete(forAccount: testAccount)

        #expect(KeychainStore.get(forAccount: testAccount) == nil)
    }

    @Test func setOverwritesAPreviousValueForTheSameAccount() {
        KeychainStore.set("first-value", forAccount: testAccount)
        KeychainStore.set("second-value", forAccount: testAccount)
        defer { KeychainStore.delete(forAccount: testAccount) }

        #expect(KeychainStore.get(forAccount: testAccount) == "second-value")
    }
}
