#!/usr/bin/env python3
"""Editorial and structural review for every HostPanel production locale."""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "app/static"
TEMPLATES = ROOT / "app/templates"
LOCALES = ("da", "de", "en", "es", "fi", "fr", "ja", "nb", "nl", "pl", "pt", "sv", "zh")
LOGIN_FIELDS = {
    "title", "subtitle", "username", "password", "code", "code_placeholder",
    "verify", "signin", "passkey", "skip", "language", "continue_with",
    "expired", "locked", "wrong_credentials", "attempts_left",
    "local_admin_disabled", "admin_mfa_required", "invalid_code",
    "enter_username", "passkey_unavailable", "passkey_failed",
}
PLACEHOLDER_RE = re.compile(r"(?:\{[A-Za-z_][A-Za-z0-9_.-]*\}|%\([^)]+\)[a-zA-Z]|%[sdif])")
URL_RE = re.compile(r"https?://[^\s<>]+")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
FLAG_RE = re.compile(r"(?<!\w)--[A-Za-z0-9][A-Za-z0-9_-]*")
ENV_RE = re.compile(r"\b(?:HP|HOSTPANEL)_[A-Z0-9_]+\b")
CERT_RE = re.compile(r"-----BEGIN [A-Z0-9 ]+-----|-----END [A-Z0-9 ]+-----")
PATH_RE = re.compile(r"(?<!\w)/(?:etc|opt|var|usr|home|root|tmp|srv|run|dev|proc|sys|mnt|media|boot)(?:/[A-Za-z0-9._~%+@:-]+)*")
UNSAFE_RE = re.compile(r"<(?:script|iframe|object|embed)\b|javascript:", re.I)
MOJIBAKE_RE = re.compile(r"(?:Ã[\x80-\xBF]|Â[\x80-\xBF]|â(?:€|™|œ|“|”)|ï¿½|\ufffd)")
PLURAL_HACK_RE = re.compile(
    r"\b(?:minute\(s\)|attempt\(s\)|Minute\(n\)|Versuch\(e\)|minuto\(s\)|"
    r"intento\(s\)|minute\(s\)|tentative\(s\)|minutt\(er\)|poging\(en\)|"
    r"minut\(ter\)|minut\(er\))\b",
    re.I,
)
PLURAL_SENSITIVE_KEYS = {
    "runtime.v221.applied.to.count.account.s.using.mode.f3e7762510",
    "runtime.v221.count.certificate.s.expire.soon.7c57b8c768",
    "runtime.v221.count.key.s.under.prefix.suffix.0798882b4d",
    "runtime.v221.isolated.count.account.s.49057fc510",
    "runtime.v221.parsed.lines.line.s.across.logs.log.s.e474cf52b3",
    "runtime.v221.parsed.logs.log.s.bytes.newly.counted.d80954c2e6",
    "runtime.v221.removed.count.key.s.76cb9d9e30",
    "runtime.v221.sent.to.count.user.s.f9d93c8382",
    "runtime.v221.table.total.row.s.total.showing.shown.e1572f2d9c",
    "ui.firewall.window_remaining",
    "ui.platform.valid_policy",
    "ui.fleet.eligible_policy",
}
GENERIC_PLURAL_HACK_RE = re.compile(r"\b[\wÀ-ÖØ-öø-ÿĀ-ž]+(?:\([^)]{1,10}\)|/[\wÀ-ÖØ-öø-ÿĀ-ž]+)\b")
SOURCE_ALLOW_RE = re.compile(
    r"(?:https?://|/|@|\.tar\.gz|\.git|\.php|\.html|SELECT\s|BEGIN (?:PGP|PRIVATE|CERTIFICATE)|"
    r"^[*]|^[a-z0-9_.-]+(?:,[a-z0-9_.:-]+)+$|\b(?:API|DNS|PHP|SQL|SSH|SSL|TLS|Redis|"
    r"PostgreSQL|MariaDB|WordPress|Docker|Podman|Git|MFA|SSO|OIDC|SAML|CDN|DKIM|DMARC|"
    r"SPF|TTL|JSON|YAML|CSV|SHA-256|Ed25519|HostPanel|WebAuthn|Passkey|JavaScript|Python SDK)\b)",
    re.I,
)
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž]+")
CJK_REPEAT_RE = re.compile(r"([\u3400-\u9fff])\1{2,}")
PT_FOREIGN_CHAR_RE = re.compile(r"[èòìùëïÿ]")
PT_FOREIGN_WORD_RE = re.compile(
    r"(?i)(?<![\w])(?:aggiunto|aggiunta|aggiunte|cargando|creata|creato|"
    r"configurazione|données|eliminato|eliminata|fichier|fichiers|logiciel|"
    r"mensajear|mensaje|mensajes|nessun|ninguna|ninguno|nodo|nodos|non|"
    r"profilo|regola|regules|revoluta|seleccione|serveur|sessione|súbdo|"
    r"tabla|trabailleurs|utente|utenti)(?![\w])"
)
JA_BAD_PHRASE_RE = re.compile(
    r"(?:批判的発見|禁制藩|親藩|転校|転職|節約する|求人|刑務所|清掃年齢|"
    r"藩名|発砲する|余計なこと|config は$)"
)
SV_REQUIRED_TRANSLATION_KEYS = frozenset(
    """
ui.64.hexadecimal.characters
ui.alias.domain
ui.api.tokens.2
ui.app.password
ui.app.servers
ui.archive.days
ui.automatic.reply
ui.autossl.centre
ui.available.runtimes
ui.backup.archive
ui.backup.archive.2
ui.bandwidth.gb
ui.billing.connector
ui.blocked.addresses
ui.build.sbom
ui.caa.issuer
ui.cache.table
ui.cache.table.2
ui.certificate.pem
ui.chain.optional
ui.choose.file
ui.cleanup.age
ui.client.id
ui.client.secret
ui.connect.to
ui.contact.email
ui.controls.the
ui.copied.from
ui.cpanel.suite
ui.customer.username
ui.customer.username.2
ui.deploy.script
ui.developer.platform
ui.directadmin.capability
ui.directadmin.tools
ui.discover.installations
ui.disk.mb
ui.disk.used
ui.dns.cluster
ui.dns.provider
ui.dynamic.dns
ui.encrypted.secrets
ui.encryption.key
ui.entry.file
ui.export.sql
ui.external.subject
ui.fence.endpoint
ui.fence.provider
ui.fleet.max.cpu
ui.fleet.max.disk
ui.flush.queue
ui.generate.csr
ui.health.path
ui.historical.monitoring
ui.hostpanel.account
ui.hotlink.protection
ui.hourly.limit
ui.https.endpoint
ui.https.endpoint.2
ui.https.record
ui.https.url
ui.import.key
ui.import.selected
ui.infected.files
ui.inspect.first
ui.intermediate.certificates
ui.io.weight
ui.javascript.sdk
ui.journal.address
ui.last.refreshed
ui.last.used
ui.link.identity
ui.list.address
ui.live.site
ui.live.updates
ui.mailbox.mb
ui.min.10.characters
ui.min.10.characters.2
ui.min.10.characters.3
ui.min.12.characters
ui.min.12.characters.2
ui.my.security.2
ui.node.id
ui.node.placement
ui.node.python
ui.notification.kind
ui.operations.queue
ui.options.json
ui.outbound.pool
ui.panel.toolbar
ui.parent.domain
ui.parked.domain
ui.parked.domain.2
ui.php.pools
ui.php.settings.table
ui.php.settings.table.2
ui.php.versions.table
ui.php.versions.table.2
ui.postgresql.table
ui.postgresql.table.2
ui.primary.navigation
ui.private.key
ui.production.readiness
ui.production.readiness.2
ui.python.sdk
ui.query.table
ui.query.table.2
ui.queue.restore
ui.quota.enforcement
ui.quota.mb
ui.rclone.destination
ui.reason.recorded
ui.received.from
ui.redirects.to
ui.registrar.integrations
ui.reliability.ecosystem.2
ui.reseller.acl
ui.resource.kind
ui.rootless.containers
ui.rule.id
ui.scan.all
ui.scan.malware
ui.scan.now
ui.send.to
ui.seo.scan
ui.shared.secret
ui.shared.secret.2
ui.site.tools.2
ui.ssh.user
ui.ssh.user.2
ui.stack.profiles
ui.staging.site
ui.staging.table
ui.staging.table.2
ui.system.user
ui.test.restore
ui.test.update
ui.turn.off
ui.turn.on
ui.unified.subaccounts
ui.url.path
ui.warn.at
ui.wordpress.smart.updates
""".split()
)

SOURCE_LITERAL_ALLOW = {
    ".htaccess", "CLI", "CRON", "DNSSEC", "FTP", "HTTPS URL", "ID", "IP",
    "NS1", "NS2", "OK", "OPcache", "OpenAPI", "SIEVE", "Shell", "URI",
    "YYYY-MM", "customer1", "dns2", "html", "imagick", "myapp", "siteuser", "www",
}
PLACEHOLDER_ONLY_RE = re.compile(r"^(?:[{}A-Za-z0-9_.:-]+|[\s→()/])+\Z")


def load(path: pathlib.Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root is not an object")
    bad = [key for key, text in value.items() if not isinstance(key, str) or not isinstance(text, str) or not text.strip()]
    if bad:
        raise ValueError(f"{path}: blank/non-string entries: {bad[:8]}")
    return value


def tokens(pattern: re.Pattern[str], value: str) -> collections.Counter[str]:
    return collections.Counter(pattern.findall(value))


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    english = load(STATIC / "i18n.en.json")
    english_keys = list(english)
    technical_patterns = (URL_RE, EMAIL_RE, FLAG_RE, ENV_RE, CERT_RE, PATH_RE)

    for locale in LOCALES:
        path = STATIC / f"i18n.{locale}.json"
        try:
            catalog = load(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(str(exc))
            continue
        if list(catalog) != english_keys:
            failures.append(f"{locale}: key parity/order mismatch")
        identical_natural = []
        for key, source in english.items():
            translated = catalog.get(key, "")
            if tokens(PLACEHOLDER_RE, source) != tokens(PLACEHOLDER_RE, translated):
                failures.append(f"{locale}:{key}: placeholder multiplicity mismatch")
            for pattern in technical_patterns:
                if tokens(pattern, source) != tokens(pattern, translated):
                    failures.append(f"{locale}:{key}: protected technical token mismatch")
            if UNSAFE_RE.search(translated):
                failures.append(f"{locale}:{key}: unsafe markup")
            if MOJIBAKE_RE.search(translated):
                failures.append(f"{locale}:{key}: mojibake")
            if PLURAL_HACK_RE.search(translated):
                failures.append(f"{locale}:{key}: parenthetical plural workaround")
            if key in PLURAL_SENSITIVE_KEYS and GENERIC_PLURAL_HACK_RE.search(translated):
                failures.append(f"{locale}:{key}: slash/parenthetical plural workaround")
            if CJK_REPEAT_RE.search(translated):
                failures.append(f"{locale}:{key}: repeated CJK corruption")
            if locale == "pt":
                if PT_FOREIGN_CHAR_RE.search(translated):
                    failures.append(f"{locale}:{key}: non-Portuguese accented character")
                if PT_FOREIGN_WORD_RE.search(translated):
                    failures.append(f"{locale}:{key}: foreign-language contamination")
            if locale == "ja" and JA_BAD_PHRASE_RE.search(translated):
                failures.append(f"{locale}:{key}: known machine-translation mistranslation")
            if locale == "sv" and key in SV_REQUIRED_TRANSLATION_KEYS and source == translated:
                failures.append(f"{locale}:{key}: untranslated reviewed Swedish UI label")
            source_identical = locale != "en" and source == translated and not SOURCE_ALLOW_RE.search(source)
            if source_identical and source not in SOURCE_LITERAL_ALLOW and not PLACEHOLDER_ONLY_RE.fullmatch(source):
                if locale in {"ja", "zh"} and WORD_RE.search(source):
                    failures.append(f"{locale}:{key}: untranslated natural-language label")
                elif len(WORD_RE.findall(source)) >= 3:
                    identical_natural.append(key)
        if identical_natural:
            warnings.append(f"{locale}: {len(identical_natural)} source-identical natural strings: {identical_natural[:12]}")
        print(f"{locale}: {len(catalog)}/{len(english)} keys; source-identical review {len(identical_natural)}")

    login_js = (STATIC / "login.js").read_text(encoding="utf-8")
    match = re.search(r"const LOGIN_MESSAGES=(\{.*?\});\nfunction formatLoginMessage", login_js, re.S)
    if not match:
        failures.append("login.js: LOGIN_MESSAGES object not found")
    else:
        messages = json.loads(match.group(1))
        if tuple(messages) != LOCALES:
            failures.append(f"login.js: locale registry mismatch: {tuple(messages)}")
        for locale in LOCALES:
            fields = set(messages.get(locale, {}))
            if fields != LOGIN_FIELDS:
                failures.append(f"login.js:{locale}: login field mismatch")
            for key in LOGIN_FIELDS:
                source = messages.get("en", {}).get(key, "")
                translated = messages.get(locale, {}).get(key, "")
                if tokens(PLACEHOLDER_RE, source) != tokens(PLACEHOLDER_RE, translated):
                    failures.append(f"login.js:{locale}:{key}: placeholder mismatch")
                if PLURAL_HACK_RE.search(translated):
                    failures.append(f"login.js:{locale}:{key}: parenthetical plural workaround")
                if CJK_REPEAT_RE.search(translated):
                    failures.append(f"login.js:{locale}:{key}: repeated CJK corruption")
                if locale == "pt" and (PT_FOREIGN_CHAR_RE.search(translated) or PT_FOREIGN_WORD_RE.search(translated)):
                    failures.append(f"login.js:{locale}:{key}: foreign-language contamination")
                if locale == "ja" and JA_BAD_PHRASE_RE.search(translated):
                    failures.append(f"login.js:{locale}:{key}: known machine-translation mistranslation")

    expected_codes = list(LOCALES)
    for path, select_id in ((TEMPLATES / "panel.html", "languageSelect"), (TEMPLATES / "login.html", "loginLanguage")):
        text = path.read_text(encoding="utf-8")
        select = re.search(rf'<select[^>]+id="{select_id}"[^>]*>(.*?)</select>', text, re.S)
        if not select:
            failures.append(f"{path.name}: {select_id} not found")
            continue
        codes = re.findall(r'<option value="([^"]+)"', select.group(1))
        if codes != expected_codes:
            failures.append(f"{path.name}: language options mismatch: {codes}")

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if failures:
        print("Localization editorial review failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"Localization editorial review passed for {len(LOCALES)} locales.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
