from __future__ import annotations

import ast
import pathlib
import unittest


class FoundationContractExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).parents[1]

    def _methods(self, relative: str, class_name: str) -> set[str]:
        tree = ast.parse((self.root / relative).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return {item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.fail(f"missing class {class_name}")

    def test_token_store_exposes_two_phase_and_public_read_contracts(self):
        methods = self._methods("tools/hostpanel_api_tokens/store.py", "ApiTokenStore")
        self.assertTrue({"authenticate_identity", "authenticate", "get_service_account", "list_service_accounts"} <= methods)

    def test_control_store_exposes_public_job_lookup(self):
        methods = self._methods("tools/hostpanel_api_control/store.py", "ApiControlStore")
        self.assertIn("get_job", methods)

    def test_http_request_context_carries_exact_dispatch_time(self):
        source = (self.root / "tools/hostpanel_api_http/model.py").read_text(encoding="utf-8")
        dispatch = (self.root / "tools/hostpanel_api_http/dispatch.py").read_text(encoding="utf-8")
        self.assertIn("now: int", source)
        self.assertIn("now=timestamp", dispatch)

    def test_route_query_contract_is_generated_into_openapi(self):
        route = (self.root / "tools/hostpanel_api_http/route.py").read_text(encoding="utf-8")
        openapi = (self.root / "tools/hostpanel_api_http/openapi.py").read_text(encoding="utf-8")
        self.assertIn("query_parameters", route)
        self.assertIn("route.query_parameters", openapi)


if __name__ == "__main__":
    unittest.main()
