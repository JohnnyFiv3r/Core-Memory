from __future__ import annotations

import json
import unittest
from pathlib import Path


class TestHttpContractAsyncOpsSlice52A(unittest.TestCase):
    def test_contract_declares_async_ops_endpoints(self):
        repo = Path(__file__).resolve().parents[1]
        contract = json.loads((repo / "docs" / "contracts" / "http_api.v1.json").read_text(encoding="utf-8"))
        eps = {(str(e.get("method") or "").upper(), str(e.get("path") or "")) for e in (contract.get("endpoints") or [])}

        self.assertIn(("GET", "/v1/ops/async-jobs/status"), eps)
        self.assertIn(("POST", "/v1/ops/async-jobs/enqueue"), eps)
        self.assertIn(("POST", "/v1/ops/async-jobs/run"), eps)

    def test_contract_endpoints_exist_in_http_server_routes(self):
        try:
            from core_memory.integrations.http.server import app
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"fastapi stack unavailable: {exc}")

        route_pairs = {(m.upper(), r.path) for r in app.routes for m in getattr(r, "methods", set()) if m in {"GET", "POST"}}
        self.assertIn(("GET", "/v1/ops/async-jobs/status"), route_pairs)
        self.assertIn(("POST", "/v1/ops/async-jobs/enqueue"), route_pairs)
        self.assertIn(("POST", "/v1/ops/async-jobs/run"), route_pairs)


if __name__ == "__main__":
    unittest.main()
