import Foundation
import Testing
@testable import SigilDev

/// Fixtures below are trimmed but structurally real — captured from actual
/// responses observed from the governed backend during development, not
/// invented shapes. These tests exist to catch silent decode failures
/// (which the app already treats as "Bridge Unreachable" rather than a
/// crash — see `HermesBridgeDataProvider`) before they reach a user.
struct BridgeDecodingTests {
    private func decode<Result: Decodable>(_ type: Result.Type, _ json: String) throws -> Result {
        try JSONDecoder().decode(Result.self, from: Data(json.utf8))
    }

    @Test func decodesSuccessfulBridgeEnvelope() throws {
        let json = #"{"ok": true, "result": {"bridge_version": "2.1", "status": "ok", "mode": "local-read-only"}}"#
        let envelope = try decode(BridgeEnvelope<BridgeHealthResult>.self, json)
        #expect(envelope.ok == true)
        #expect(envelope.result?.bridgeVersion == "2.1")
        #expect(envelope.result?.mode == "local-read-only")
    }

    @Test func decodesFailedBridgeEnvelope() throws {
        let json = #"{"ok": false, "error": "invalid_payload", "message": "bad request"}"#
        let envelope = try decode(BridgeEnvelope<BridgeHealthResult>.self, json)
        #expect(envelope.ok == false)
        #expect(envelope.result == nil)
        #expect(envelope.error == "invalid_payload")
    }

    @Test func decodesRuntimeSnapshotResult() throws {
        let json = #"""
        {
          "runtime_visibility": {
            "operational_state": "stopped",
            "health": "healthy",
            "connection_state": "connected",
            "automation_mode": "monthly-authorized-paper-execution",
            "paper_execution_available": true,
            "counts": {"cycles": 0, "proposals": 0, "executions": 0, "reconciliation": 0, "audit_events": 1}
          }
        }
        """#
        let result = try decode(RuntimeSnapshotResult.self, json)
        #expect(result.runtimeVisibility.health == "healthy")
        #expect(result.runtimeVisibility.operationalState == "stopped")
        #expect(result.runtimeVisibility.paperExecutionAvailable == true)
        #expect(result.runtimeVisibility.counts.auditEvents == 1)
    }

    @Test func decodesPrimeFleetStatusWithRealNodeShape() throws {
        let json = #"""
        {
          "configured": true,
          "reachable": true,
          "base_url": "http://100.64.0.1:8743",
          "nodes": [
            {"natural_key": "mac", "role": "mac", "connection_state": "connected",
             "capabilities": ["worker_heartbeat", "local_model_inference"],
             "model_inventory": ["gemma4:12b"]}
          ],
          "certification": {"status": "certified", "evidence_ref": "prime_certification:abc"}
        }
        """#
        let result = try decode(PrimeFleetStatusResult.self, json)
        #expect(result.configured == true)
        #expect(result.reachable == true)
        #expect(result.nodes.count == 1)
        #expect(result.nodes.first?.naturalKey == "mac")
        #expect(result.nodes.first?.connectionState == "connected")
        #expect(result.nodes.first?.modelInventory == ["gemma4:12b"])
        #expect(result.certification.status == "certified")
    }

    @Test func decodesPrimeFleetStatusWhenNotConfigured() throws {
        let json = #"""
        {"configured": false, "reachable": false, "base_url": null, "nodes": [],
         "certification": {"status": "unknown", "evidence_ref": null}}
        """#
        let result = try decode(PrimeFleetStatusResult.self, json)
        #expect(result.configured == false)
        #expect(result.baseURL == nil)
        #expect(result.nodes.isEmpty)
    }

    @Test func decodesHistoricalHydraLiveNodeWithoutMakingItExpected() throws {
        let json = #"""
        {
          "configured": true,
          "reachable": true,
          "base_url": "http://prime.invalid:8743",
          "nodes": [
            {"natural_key": "hydra-live", "role": "hydra_live",
             "connection_state": "disconnected", "model_inventory": []}
          ],
          "certification": {"status": "unknown", "evidence_ref": null}
        }
        """#
        let result = try decode(PrimeFleetStatusResult.self, json)
        #expect(result.nodes.first?.naturalKey == "hydra-live")
        #expect(result.nodes.first?.role == "hydra_live")
    }

    @Test func decodesPaperExecutionStatus() throws {
        let json = #"""
        {
          "environment": "paper", "live_execution": false, "broker": "alpaca_paper",
          "broker_submission": false, "activated": false, "paused": false, "kill_switch": true,
          "degraded_conditions": [], "unmanaged_position_symbols": [],
          "open_positions": 0, "open_orders": 0,
          "deployed_paper_capital": "0", "remaining_governed_allocation": "1000",
          "last_reconciliation": null,
          "last_order_intent": null, "last_submitted_order": null, "last_fill": null, "last_rejection": null
        }
        """#
        let result = try decode(PaperExecutionStatus.self, json)
        #expect(result.liveExecution == false)
        #expect(result.killSwitch == true)
        #expect(result.activated == false)
        #expect(result.lastReconciliation == nil)
    }

    @Test func decodesPaperCollectionResult() throws {
        let json = #"""
        {"broker_submission": false, "revision": 1, "offset": 0, "limit": 50, "total": 1,
         "has_more": false, "items": [{"symbol": "AAPL", "side": "buy"}]}
        """#
        let result = try decode(PaperCollectionResult.self, json)
        #expect(result.total == 1)
        #expect(result.hasMore == false)
        #expect(result.items.count == 1)
        #expect(result.items.first?["symbol"] == .string("AAPL"))
    }

    @Test func decodesGovernedNewsStatusWhenEmpty() throws {
        let json = #"""
        {"status": "empty", "headline_count": 0, "symbol_count": 0, "last_collected_at": null, "headlines": []}
        """#
        let result = try decode(GovernedNewsStatus.self, json)
        #expect(result.status == "empty")
        #expect(result.headlineCount == 0)
        #expect(result.headlines.isEmpty)
    }

    @Test func decodesAIStatusMacOllamaWithoutProbeFields() throws {
        // Matches Sigil 3.7's real environment: probe_service is off, so
        // service_reachable/installed_models/running_models are absent
        // entirely, not present-but-empty. Decoding must not fail or
        // silently invent values for them.
        let json = #"""
        {
          "enabled": false, "service_state": "disabled", "local_gemma_health": "disabled",
          "configured_model_count": 0, "available_model_count": 0, "registry_revision": "unconfigured",
          "mac_ollama": {"enabled": false, "roles": {}},
          "evidence_record_count": 0, "artifact_count": 0,
          "evidence_ledger_health": "empty", "artifact_store_health": "empty",
          "latest_analysis_summary": null,
          "orchestration": {"enabled": false, "health": "disabled", "active_count": 0, "completed_count": 0, "failed_count": 0, "paused_count": 0},
          "fleet": {
            "registered_node_count": 0, "healthy_node_count": 0,
            "nodes": {"titan": null, "mac": null, "prime": null},
            "latest_route": null, "latest_failover": null,
            "active_tasks": 0, "queued_tasks": 0, "completion_unknown_tasks": 0, "clock_warnings": 0
          }
        }
        """#
        let result = try decode(AIStatusResult.self, json)
        #expect(result.macOllama.serviceReachable == nil)
        #expect(result.macOllama.installedModels == nil)
        #expect(result.fleet.nodes.titan == nil)
        #expect(result.orchestration.enabled == false)
    }
}
