from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MANUFACTURING_CONFIG_FILE", str(ROOT / "config.example.json"))

from domain import bootstrap, context, energy_comparison, scenario, zone_summary
from server import Handler, ThreadingHTTPServer, main as serve
from foundry import run, validate_result


class CalculationTests(unittest.TestCase):
    def test_anomaly_is_observation_not_diagnosis(self):
        data = bootstrap()
        self.assertEqual(data["review_count"], 1)
        self.assertEqual(data["average_cycle_seconds"], 93)
        self.assertEqual(context("Z02")["selected_zone"]["name"], "Ensamblaje")
        selected = context("Z02")["selected_zone"]
        self.assertEqual(selected["deviation_pct"], 18)
        self.assertTrue(selected["needs_review"])
        self.assertEqual(selected["status"], "Revisión pendiente")
        self.assertFalse(data["fabric_connected"])

    def test_equal_energy_and_exact_costs(self):
        result = energy_comparison(scenario())
        self.assertEqual([plan["cost_eur"] for plan in result["plans"]], [90, 60])
        self.assertEqual([plan["renewable_kwh"] for plan in result["plans"]], [150, 350])
        self.assertEqual(result["difference_eur"], 30)
        self.assertEqual(result["difference_pct"], 33.3)
        self.assertEqual(result["renewable_delta_pp"], 40)
        self.assertEqual(result["energy_saved_kwh"], 0)

    def test_invalid_reference_or_zone_fails(self):
        with self.assertRaisesRegex(ValueError, "positiva"):
            zone_summary({"baseline_cycle_seconds": 0, "observed_cycle_seconds": 10}, 15)
        with self.assertRaisesRegex(ValueError, "desconocida"):
            context("industrial-secret")

    def test_ineligible_alternative_is_not_presented_as_valid(self):
        data = deepcopy(scenario())
        data["energy_comparison"]["plans"][1]["service_eligible"] = False
        with self.assertRaisesRegex(ValueError, "condición"):
            energy_comparison(data)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_bootstrap_explicitly_discloses_synthetic_mode(self):
        with urllib.request.urlopen(self.base + "/api/bootstrap") as response:
            data = json.load(response)
            self.assertEqual(data["data_mode"], "synthetic_snapshot")
            self.assertFalse(data["fabric_connected"])

    def test_unknown_route_and_cross_origin_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base + "/../../config.json")
        self.assertEqual(error.exception.code, 404)
        request = urllib.request.Request(self.base + "/api/bootstrap", headers={"Origin": "https://untrusted.example"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)

    def test_bad_json_shapes_are_not_cloud_requests(self):
        for value in ([], {"action": "energy"}, {"action": "energy", "zone_id": "Z02", "extra": "no"}):
            request = urllib.request.Request(
                self.base + "/api/ask", data=json.dumps(value).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 400)

    @patch("server.run", side_effect=RuntimeError("Azure unavailable"))
    def test_cloud_error_is_explicit_not_canned_success(self, run):
        request = urllib.request.Request(
            self.base + "/api/ask", data=json.dumps({"action": "overview", "zone_id": "Z02"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 502)
        body = json.load(error.exception)
        self.assertIn("No se muestra una respuesta simulada", body["error"])
        self.assertNotIn("answer", body)


class AgentBoundaryTests(unittest.TestCase):
    @patch("foundry.hosted_client")
    def test_invalid_action_or_zone_never_reaches_foundry(self, client):
        with self.assertRaises(ValueError):
            run("execute_pump", "Z02")
        with self.assertRaises(ValueError):
            run("overview", "invalid-zone")
        client.assert_not_called()

    @patch("foundry.hosted_client")
    def test_answer_without_snapshot_read_is_not_a_success(self, factory):
        client = factory.return_value.__enter__.return_value
        client.responses.create.return_value = SimpleNamespace(
            status="completed", output_text='{"answer":"Una explicación sin evidencias."}',
        )
        with self.assertRaisesRegex(RuntimeError, "no acredita recuperación"):
            run("overview", "Z02")

    def test_unverified_or_wrong_knowledge_base_is_rejected(self):
        for iq in (
            {"verified": False}, {"verified": True, "knowledge_base": "other"},
            {"verified": True, "knowledge_base": "manufacturing-iq", "document_id": "PR-PROD-017", "retrieved_text": ""},
        ):
            with self.subTest(iq=iq), self.assertRaisesRegex(RuntimeError, "recuperación"):
                validate_result({"answer": "Una respuesta.", "iq": iq})


class StartupTests(unittest.TestCase):
    @patch.dict(os.environ, {"REQUIRE_ENTRA_AUTH": "false"})
    @patch("sys.argv", ["server.py", "--host", "0.0.0.0"])
    @patch("server.ThreadingHTTPServer")
    def test_public_binding_requires_authentication(self, server):
        with self.assertRaisesRegex(RuntimeError, "REQUIRE_ENTRA_AUTH=true"):
            serve()
        server.assert_not_called()


if __name__ == "__main__":
    unittest.main()
