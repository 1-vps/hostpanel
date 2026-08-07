from __future__ import annotations

import base64
import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = ROOT / ".buildkite/operator/verify-pipeline-state.py"
CONTRACT = ROOT / ".buildkite/scripts/run-pipeline-contract.sh"
CODEOWNERS = ROOT / ".github/CODEOWNERS"

spec = importlib.util.spec_from_file_location("hostpanel_signature_metadata", VERIFIER)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def encode_header(payload: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")


def signature_value(header: dict | None = None, signature: str = "b3BhcXVl") -> str:
    if header is None:
        header = {"alg": "EdDSA", "kid": "hostpanel-2026-08"}
    return f"{encode_header(header)}..{signature}"


class BuildkiteSignatureMetadataTests(unittest.TestCase):
    def test_reviewed_detached_jws_header_passes(self) -> None:
        header = module.decode_jws_header(signature_value())
        self.assertEqual(header, {"alg": "EdDSA", "kid": "hostpanel-2026-08"})

    def test_extra_protected_header_parameters_are_rejected(self) -> None:
        for key, value in (
            ("jku", "https://attacker.invalid/jwks.json"),
            ("crit", ["exp"]),
            ("typ", "JWT"),
        ):
            with self.subTest(key=key):
                header = {"alg": "EdDSA", "kid": "hostpanel-2026-08", key: value}
                with self.assertRaises(module.VerificationError):
                    module.decode_jws_header(signature_value(header))

    def test_invalid_base64url_characters_are_rejected(self) -> None:
        good_header = encode_header({"alg": "EdDSA", "kid": "hostpanel-2026-08"})
        for value in (
            f"{good_header}=..b3BhcXVl",
            f"{good_header}..b3Bh+XVl",
            f"{good_header}..b3Bh/XVl",
            f"{good_header}..b3Bh=XVl",
        ):
            with self.subTest(value=value):
                with self.assertRaises(module.VerificationError):
                    module.decode_jws_header(value)

    def test_detached_jws_shape_is_exact(self) -> None:
        header = encode_header({"alg": "EdDSA", "kid": "hostpanel-2026-08"})
        for value in (
            f"{header}.payload.b3BhcXVl",
            f"{header}..",
            f".{header}.b3BhcXVl",
            f"{header}...b3BhcXVl",
        ):
            with self.subTest(value=value):
                with self.assertRaises(module.VerificationError):
                    module.decode_jws_header(value)

    def test_signature_metadata_pins_algorithm_key_and_signed_fields(self) -> None:
        step = {
            "signature": {
                "algorithm": "EdDSA",
                "signed_fields": ["command", "env", "matrix", "plugins", "repository_url"],
                "value": signature_value(),
            }
        }
        module.verify_signature_metadata(step, required=True)
        for mutation in ("algorithm", "kid", "signed_fields"):
            changed = json.loads(json.dumps(step))
            if mutation == "algorithm":
                changed["signature"]["algorithm"] = "ES512"
            elif mutation == "kid":
                changed["signature"]["value"] = signature_value(
                    {"alg": "EdDSA", "kid": "other-key"}
                )
            else:
                changed["signature"]["signed_fields"].remove("env")
            with self.subTest(mutation=mutation):
                with self.assertRaises(module.VerificationError):
                    module.verify_signature_metadata(changed, required=True)

    def test_pipeline_contract_and_codeowners_cover_signature_tests(self) -> None:
        self.assertIn(
            "tests.test_buildkite_signature_metadata",
            CONTRACT.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "/tests/test_buildkite_signature_metadata.py @1-vps",
            CODEOWNERS.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
