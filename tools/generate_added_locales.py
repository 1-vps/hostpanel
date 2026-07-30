#!/usr/bin/env python3
"""Generate draft HostPanel locale catalogs with a reproducible local MT model.

The script deliberately preserves stable keys, placeholders, URLs, commands,
paths, identifiers, product names, and code-like fragments. Generated catalogs
remain drafts until the editorial checks and application regression tests pass.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
from typing import Iterable

import torch
from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

MODEL_ID = "facebook/m2m100_418M"
TARGETS = {"ja": "ja", "pt": "pt", "zh": "zh"}

PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"https?://[^\s<>]+")
PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:[A-Za-z0-9._~%+@:-]+/)*[A-Za-z0-9._~%+@:-]*")
FLAG_RE = re.compile(r"(?<!\w)--[A-Za-z0-9][A-Za-z0-9_-]*")
ENV_RE = re.compile(r"\b(?:HP|HOSTPANEL)_[A-Z0-9_]+\b")
VERSION_RE = re.compile(r"\bv?\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?\b")
BACKTICK_RE = re.compile(r"`[^`]+`")
FORMAT_RE = re.compile(r"%(?:\([^)]+\))?[#0 +\-]?(?:\d+|\*)?(?:\.\d+|\.\*)?[diouxXeEfFgGcrs%]")
CERT_RE = re.compile(r"-----BEGIN [A-Z0-9 ]+-----|-----END [A-Z0-9 ]+-----")
SCOPE_RE = re.compile(r"^[A-Za-z0-9_.:-]+(?:,[A-Za-z0-9_.:-]+)+$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(?:json|ya?ml|toml|ini|conf|pem|key|crt|csr|sql|csv|tsv|txt|log|zip|tar|gz|tgz|deb|rpm|php|py|js|css|html?)$")

TECH_TERMS = (
    "HostPanel", "WordPress", "WooCommerce", "Redis", "PostgreSQL", "MariaDB",
    "MySQL", "SQLite", "OpenLiteSpeed", "LiteSpeed", "Nginx", "Apache",
    "PHP", "PHP-FPM", "LSPHP", "Docker", "Podman", "Git", "GitHub",
    "Cloudflare", "PowerDNS", "Hetzner", "DigitalOcean", "Vultr", "Linode",
    "Akamai", "AWS", "Azure", "EC2", "DNS", "DNSSEC", "DKIM", "DMARC",
    "SPF", "SSH", "SFTP", "FTP", "FTPS", "TLS", "SSL", "HTTP", "HTTPS",
    "API", "REST", "JSON", "YAML", "CSV", "SQL", "MFA", "SSO", "OIDC",
    "SAML", "WebAuthn", "Passkey", "WAF", "CDN", "DDoS", "IPv4", "IPv6",
    "IMAP", "IMAPSieve", "SMTP", "Exim", "Postfix", "Dovecot", "Rspamd",
    "Roundcube", "systemd", "cron", "crontab", "UFW", "firewalld", "SELinux",
    "AppArmor", "Ed25519", "SHA-256", "HMAC", "JTI", "UUID", "FQDN",
    "OPcache", "JIT", "WebDAV", "PWA", "WebSocket", "ACME", "Let's Encrypt",
)
TECH_RE = re.compile(
    r"(?<![\w])(?:" + "|".join(sorted((re.escape(term) for term in TECH_TERMS), key=len, reverse=True)) + r")(?![\w])",
    re.IGNORECASE,
)

TOKEN_PATTERNS = (
    BACKTICK_RE, CERT_RE, URL_RE, EMAIL_RE, PLACEHOLDER_RE, ENV_RE, FLAG_RE,
    PATH_RE, FORMAT_RE, VERSION_RE, TECH_RE,
)


def should_preserve(text: str) -> bool:
    value = text.strip()
    if not value or not re.search(r"[A-Za-z]", value):
        return True
    if value.startswith("-----BEGIN ") or value.startswith("-----END "):
        return True
    if URL_RE.fullmatch(value) or EMAIL_RE.fullmatch(value) or PATH_RE.fullmatch(value):
        return True
    if SCOPE_RE.fullmatch(value) or FILENAME_RE.fullmatch(value):
        return True
    if re.fullmatch(r"[A-Z0-9][A-Z0-9+./_ -]{1,40}", value) and len(value.split()) <= 4:
        return True
    if re.fullmatch(r"[A-Za-z0-9_.:/+@-]+", value) and not re.search(r"\s", value):
        return True
    return False


def protect(text: str) -> tuple[str, list[str]]:
    matches: list[tuple[int, int, str]] = []
    for pattern in TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), match.group(0)))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str]] = []
    end = -1
    for item in matches:
        if item[0] >= end:
            selected.append(item)
            end = item[1]
    if not selected:
        return text, []
    pieces: list[str] = []
    tokens: list[str] = []
    cursor = 0
    for start, stop, original in selected:
        pieces.append(text[cursor:start])
        marker = f"ZXQTK{len(tokens)}QXZ"
        pieces.append(marker)
        tokens.append(original)
        cursor = stop
    pieces.append(text[cursor:])
    return "".join(pieces), tokens


def restore(text: str, tokens: list[str]) -> str:
    result = text
    for index, original in enumerate(tokens):
        marker = f"ZXQTK{index}QXZ"
        flexible = re.compile(r"Z\s*X\s*Q\s*T\s*K\s*" + str(index) + r"\s*Q\s*X\s*Z", re.IGNORECASE)
        result, count = flexible.subn(lambda _match, value=original: value, result)
        if count == 0:
            raise ValueError(f"protected token {marker} was lost")
    return result.strip()


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def translate_values(
    tokenizer: M2M100Tokenizer,
    model: M2M100ForConditionalGeneration,
    values: list[str],
    target: str,
    batch_size: int,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    translated: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    pending: list[tuple[str, str, list[str]]] = []
    for value in values:
        if should_preserve(value):
            translated[value] = value
            continue
        protected, tokens = protect(value)
        pending.append((value, protected, tokens))

    tokenizer.src_lang = "en"
    forced_bos = tokenizer.get_lang_id(target)
    for batch_number, batch in enumerate(chunks([item[1] for item in pending], batch_size), start=1):
        offset = (batch_number - 1) * batch_size
        encoded = tokenizer(
            batch,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512,
        )
        with torch.inference_mode():
            output = model.generate(
                **encoded,
                forced_bos_token_id=forced_bos,
                num_beams=3,
                max_new_tokens=512,
                early_stopping=True,
            )
        decoded = tokenizer.batch_decode(output, skip_special_tokens=True)
        for index, candidate in enumerate(decoded):
            source, _protected, tokens = pending[offset + index]
            try:
                restored = restore(candidate, tokens)
                if sorted(PLACEHOLDER_RE.findall(restored)) != sorted(PLACEHOLDER_RE.findall(source)):
                    raise ValueError("placeholder mismatch")
                translated[source] = restored
            except ValueError as exc:
                translated[source] = source
                failures.append({"source": source, "candidate": candidate, "error": str(exc)})
        print(f"{target}: translated {min(offset + len(batch), len(pending))}/{len(pending)}", flush=True)
    return translated, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=pathlib.Path)
    parser.add_argument("output_dir", type=pathlib.Path)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    english_path = args.source_root / "app" / "static" / "i18n.en.json"
    english = json.loads(english_path.read_text(encoding="utf-8"))
    if not isinstance(english, dict) or not english:
        raise SystemExit("English catalog is missing or invalid")

    value_keys: dict[str, list[str]] = collections.defaultdict(list)
    for key, value in english.items():
        if not isinstance(value, str):
            raise SystemExit(f"non-string English value: {key}")
        value_keys[value].append(key)
    unique_values = list(value_keys)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    tokenizer = M2M100Tokenizer.from_pretrained(MODEL_ID)
    model = M2M100ForConditionalGeneration.from_pretrained(MODEL_ID)
    model.eval()

    report: dict[str, object] = {
        "model": MODEL_ID,
        "source_keys": len(english),
        "unique_source_values": len(unique_values),
        "targets": {},
    }
    for locale, model_language in TARGETS.items():
        translations, failures = translate_values(
            tokenizer, model, unique_values, model_language, args.batch_size
        )
        catalog = {key: translations[value] for key, value in english.items()}
        destination = args.output_dir / f"i18n.{locale}.json"
        destination.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        identical_natural = [
            key for key, value in catalog.items()
            if value == english[key] and not should_preserve(value)
        ]
        report["targets"][locale] = {
            "keys": len(catalog),
            "fallback_failures": failures,
            "source_identical_natural_keys": identical_natural,
        }

    (args.output_dir / "machine-translation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
