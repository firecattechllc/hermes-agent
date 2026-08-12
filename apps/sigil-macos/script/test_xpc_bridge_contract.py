#!/usr/bin/env python3
"""Deterministic source and project contract tests for the embedded bridge."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT = ROOT / "Sigil/Sigil.xcodeproj/project.pbxproj"
SERVICE = ROOT / "Sigil/HermesBridgeService/main.swift"
ENTITLEMENTS = ROOT / "Sigil/HermesBridgeService/HermesBridgeService.entitlements"
CONTROLLER = ROOT / "Sigil/Sigil/Services/HermesBridgeServiceController.swift"


class XPCBridgeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = PROJECT.read_text(encoding="utf-8")
        cls.service = SERVICE.read_text(encoding="utf-8")
        cls.entitlements = ENTITLEMENTS.read_text(encoding="utf-8")
        cls.controller = CONTROLLER.read_text(encoding="utf-8")

    def test_xpc_target_is_embedded_and_code_signed_on_copy(self):
        self.assertIn('productType = "com.apple.product-type.xpc-service";', self.project)
        self.assertIn("Embed XPC Services", self.project)
        self.assertIn("CodeSignOnCopy", self.project)

    def test_service_is_sandboxed_and_has_only_required_network_entitlements(self):
        self.assertIn("com.apple.security.app-sandbox", self.entitlements)
        self.assertIn("com.apple.security.network.client", self.entitlements)
        self.assertIn("com.apple.security.network.server", self.entitlements)
        self.assertNotIn("temporary-exception", self.entitlements)

    def test_loopback_port_is_literal_and_not_public(self):
        self.assertIn(".ipv4(.loopback)", self.service)
        self.assertIn("port: 8799", self.service)
        self.assertNotIn("0.0.0.0", self.service)

    def test_parent_owns_single_connection_and_starts_automatically(self):
        self.assertIn("guard connection == nil else { return }", self.controller)
        self.assertIn("HermesBridgeServiceController.shared.start()", (ROOT / "Sigil/Sigil/App/SigilApp.swift").read_text())

    def test_only_explicit_safe_post_routes_exist(self):
        self.assertIn('method == "POST", route == "/market_universe_quotes"', self.service)
        for route in (
            "paper_execution_activate",
            "paper_execution_pause",
            "paper_execution_resume",
            "paper_execution_deactivate",
        ):
            self.assertIn(f'"/{route}"', self.service)
        for forbidden in ("order_submit", "order_preview", "reconcile_paper_orders", "emergency_paper_stop"):
            self.assertNotIn(f'route == "/{forbidden}"', self.service)

    def test_unknown_routes_are_404_and_lifecycle_actions_fail_closed(self):
        self.assertIn("send(status: 404, object: failure(\"not_found\")", self.service)
        self.assertIn('"error": "governed_backend_not_configured"', self.service)
        self.assertIn("lifecycleRoutes.contains(route)", self.service)

    def test_lifecycle_payload_is_bounded_and_shape_validated(self):
        self.assertIn("body.utf8.count <= 128", self.service)
        self.assertIn("return object.isEmpty", self.service)
        self.assertIn('failure("invalid_payload")', self.service)

    def test_payload_and_response_are_bounded(self):
        self.assertIn("data.count <= 16_384", self.service)
        self.assertIn("body.count <= 4_096", self.service)
        self.assertIn("data.count <= 1_048_576", self.service)
        self.assertIn("(1...20).contains(symbols.count)", self.service)

    def test_visible_ui_read_routes_are_explicit(self):
        for route in (
            "health", "runtime_snapshot", "ai_status", "prime_fleet_status",
            "paper_execution_status", "paper_positions", "paper_orders", "paper_fills",
            "recent_proposals", "recent_candidates", "recent_rejections", "recent_audit",
            "governed_news_status",
        ):
            self.assertIn(f'"/{route}"', self.service)

    def test_all_safety_fields_are_fail_closed(self):
        for field in ("execution_authorized", "broker_submission_available", "live_trading_enabled"):
            self.assertIn(f'"{field}": false', self.service)
        self.assertIn('"data_only": true', self.service)


if __name__ == "__main__":
    unittest.main()
