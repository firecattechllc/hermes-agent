import Foundation
import Testing
@testable import SigilDev

struct JSONValueTests {
    private func decode(_ json: String) throws -> JSONValue {
        try JSONDecoder().decode(JSONValue.self, from: Data(json.utf8))
    }

    @Test func decodesString() throws {
        #expect(try decode("\"hello\"") == .string("hello"))
    }

    @Test func decodesNumber() throws {
        #expect(try decode("42") == .number(42))
    }

    @Test func decodesBool() throws {
        #expect(try decode("true") == .bool(true))
    }

    @Test func decodesNull() throws {
        #expect(try decode("null") == .null)
    }

    @Test func decodesArray() throws {
        #expect(try decode("[1, 2, 3]") == .array([.number(1), .number(2), .number(3)]))
    }

    @Test func decodesNestedObject() throws {
        let value = try decode(#"{"symbol": "AAPL", "quantity": 10}"#)
        guard case .object(let dict) = value else {
            Issue.record("expected object")
            return
        }
        #expect(dict["symbol"] == .string("AAPL"))
        #expect(dict["quantity"] == .number(10))
    }

    @Test func displayStringFormatsIntegerLikeNumbersWithoutDecimal() {
        #expect(JSONValue.number(10).displayString == "10")
        #expect(JSONValue.number(10.5).displayString == "10.5")
    }

    @Test func displayStringForNullIsAnEmDash() {
        #expect(JSONValue.null.displayString == "—")
    }

    @Test func decodesFullPaperRecordAsJSONRecord() throws {
        let json = #"{"symbol": "AAPL", "side": "buy", "quantity": 10, "filled": true, "note": null}"#
        let record = try JSONDecoder().decode(JSONRecord.self, from: Data(json.utf8))
        #expect(record["symbol"] == .string("AAPL"))
        #expect(record["side"] == .string("buy"))
        #expect(record["quantity"] == .number(10))
        #expect(record["filled"] == .bool(true))
        #expect(record["note"] == .null)
    }
}
