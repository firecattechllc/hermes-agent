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

    def test_only_quote_post_route_exists(self):
        self.assertIn('method == "POST", route == "/market_universe_quotes"', self.service)
        for forbidden in ("order_submit", "order_preview", "paper_execution_activate", "paper_execution_resume"):
            self.assertNotIn(f'route == "/{forbidden}"', self.service)

    def test_payload_and_response_are_bounded(self):
        self.assertIn("data.count <= 16_384", self.service)
        self.assertIn("body.count <= 4_096", self.service)
        self.assertIn("data.count <= 1_048_576", self.service)
        self.assertIn("(1...20).contains(symbols.count)", self.service)

    def test_all_safety_fields_are_fail_closed(self):
        for field in ("execution_authorized", "broker_submission_available", "live_trading_enabled"):
            self.assertIn(f'"{field}": false', self.service)
        self.assertIn('"data_only": true', self.service)


if __name__ == "__main__":
    unittest.main()
