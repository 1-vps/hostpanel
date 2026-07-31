#!/usr/bin/env python3
"""Apply the core localization overlay plus reviewed language corrections."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent
CORE_PATH = ROOT / "apply_localization_overlay.py"
FINAL_OVERRIDE_FILES = (
    "catalog-final-overrides.ja-01.json",
    "catalog-final-overrides.ja-02.json",
    "catalog-final-overrides.pt-01.json",
    "catalog-final-overrides.zh-01.json",
    "catalog-final-overrides.zh-02.json",
)
EXPECTED_BASE_COUNTS = {"ja": 19, "pt": 21, "zh": 15}
EXPECTED_BASE_CANONICAL_SHA256 = (
    "98e88a7c679eb3b4342a268deac8b0548c4e9509a1769b3ffc5626411a388604"
)
EXPECTED_FINAL_COUNTS = {"ja": 91, "pt": 31, "zh": 60}
EXPECTED_FINAL_CANONICAL_SHA256 = (
    "5bbb02dfacb69ed83157a89348ac2e24da85665ffed8b6eb1866ca69ad232b5f"
)
EXPECTED_VISIBLE_COUNTS = {"ja": 110, "pt": 52, "zh": 75}
EXPECTED_VISIBLE_CANONICAL_SHA256 = (
    "6d17c244c021aa08edc4a0a14cb7c49427e9bb5653e7e36725efa82a8fc0afec"
)
PORTUGUESE_UI_OVERRIDES = {'pt': {'dialog.account_name': 'Nome da conta (opcional):',
        'dialog.cancel': 'Cancelar',
        'dialog.close': 'Fechar',
        'dialog.confirm_action': 'Confirmar ação',
        'dialog.continue': 'Continuar',
        'dialog.delete_cluster_plan': 'Excluir este plano de cluster?',
        'dialog.delete_dr_schedule': 'Excluir este agendamento de DR?',
        'dialog.delete_maintenance_plan': 'Excluir este plano de manutenção?',
        'dialog.delete_permanently': 'Excluir permanentemente?',
        'dialog.delete_plan': 'Excluir este plano de capacidade?',
        'dialog.delete_probe_schedule': 'Excluir este agendamento de sonda?',
        'dialog.delete_profile': 'Excluir este perfil de provedor?',
        'dialog.delete_recovery_plan': 'Excluir este plano de recuperação?',
        'dialog.destination_node': 'Nó de destino (opcional):',
        'dialog.enter_value': 'Insira um valor',
        'dialog.information': 'Informações',
        'dialog.move_to_trash': 'Mover {name} para a lixeira?',
        'dialog.search_files': 'Pesquisar nomes e conteúdo de arquivos:',
        'dialog.share_minutes': 'Validade do link em minutos:',
        'dialog.type_cluster_plan': 'Digite o nome do plano de cluster para continuar:',
        'dialog.type_cluster_plan_rollback': 'Digite o nome do plano de cluster para reverter:',
        'dialog.type_maintenance_plan': 'Digite o nome do plano de manutenção para continuar:',
        'dialog.type_maintenance_plan_rollback': 'Digite o nome do plano de manutenção para '
                                                 'reverter:',
        'dialog.type_recovery_plan': 'Digite o nome do plano de recuperação para continuar:',
        'dialog.type_recovery_plan_rollback': 'Digite o nome do plano de recuperação para '
                                              'reverter:',
        'dialog.value': 'Valor',
        'errors.choose_file': 'Selecione um arquivo',
        'errors.field_required': 'Campo obrigatório',
        'errors.invalid_request': 'A solicitação contém valores inválidos.',
        'errors.json_list': 'Insira uma lista JSON válida',
        'errors.popup_blocked': 'O navegador bloqueou a janela de login.',
        'errors.validation_field': '{field}: {message}',
        'route.accounts': 'Contas',
        'route.alerts': 'Alertas',
        'route.apps': 'Aplicativos',
        'route.appservers': 'Servidores de aplicativos',
        'route.audit': 'Log de auditoria',
        'route.backups': 'Backups',
        'route.cache': 'Cache Redis',
        'route.certs': 'Certificados',
        'route.cpanel': 'cPanel',
        'route.cron': 'Tarefas agendadas',
        'route.dashboard': 'Painel',
        'route.databases': 'Bancos de dados',
        'route.dbadmin': 'Gerenciador de bancos de dados',
        'route.dbusers': 'Usuários de banco de dados',
        'route.directadmin': 'DirectAdmin',
        'route.dns': 'Zonas DNS',
        'route.domains': 'Domínios',
        'route.expansion': 'Infraestrutura e ecossistema',
        'route.files': 'Gerenciador de arquivos',
        'route.firewall': 'Firewall',
        'route.fleet': 'Cluster e posicionamento',
        'route.ftp': 'Contas FTP',
        'route.git': 'Implantação Git',
        'route.governance': 'Identidade e governança',
        'route.isolation': 'Isolamento da conta',
        'route.mail': 'E-mail',
        'route.migrate': 'Migração',
        'route.mysecurity': 'Minha segurança',
        'route.php': 'Versões do PHP',
        'route.phpconf': 'Configurações do PHP',
        'route.plans': 'Planos de hospedagem',
        'route.platform': 'Suíte da plataforma',
        'route.postgres': 'PostgreSQL',
        'route.production': 'Controles de produção',
        'route.reliability': 'Confiabilidade e ecossistema',
        'route.resources': 'Disco e largura de banda',
        'route.security': 'Segurança do servidor',
        'route.sieve': 'Regras de e-mail',
        'route.sitetools': 'Ferramentas do site',
        'route.spam': 'Proteção contra spam',
        'route.ssh': 'Acesso SSH',
        'route.staging': 'Staging',
        'route.subdomains': 'Subdomínios e aliases',
        'route.support': 'Sessões de suporte',
        'route.tokens': 'Tokens de API',
        'route.waf': 'Firewall de aplicações web',
        'route.webserver': 'Servidor web',
        'route.webstats': 'Estatísticas de visitantes'}}
EXPECTED_UI_COUNTS = {"pt": 80}
EXPECTED_UI_CANONICAL_SHA256 = (
    "193ef6c9f6b0e3b36f755ace7d685109974ae30aa480bd4db9bdc01eceb2c08c"
)


def canonical_sha256(payload: dict[str, dict[str, str]]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_reviewed_payload(
    label: str,
    payload: dict[str, dict[str, str]],
    expected_counts: dict[str, int],
    expected_digest: str,
) -> None:
    if set(payload) != set(expected_counts):
        raise SystemExit(
            f"{label} locale mismatch: expected {sorted(expected_counts)}, got {sorted(payload)}"
        )
    for locale, entries in payload.items():
        if not isinstance(entries, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not value.strip()
            for key, value in entries.items()
        ):
            raise SystemExit(f"{label}: invalid reviewed entries for {locale}")
    counts = {locale: len(entries) for locale, entries in payload.items()}
    if counts != expected_counts:
        raise SystemExit(f"{label} count mismatch: expected {expected_counts}, got {counts}")
    digest = canonical_sha256(payload)
    if digest != expected_digest:
        raise SystemExit(
            f"{label} digest mismatch: expected {expected_digest}, got {digest}"
        )


def load_core():
    spec = importlib.util.spec_from_file_location("hostpanel_localization_core", CORE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit("could not load core localization overlay")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_review_files(
    overlay: pathlib.Path,
    file_names: tuple[str, ...],
    pattern: str,
    allowed_locales: frozenset[str],
    label: str,
) -> dict[str, dict[str, str]]:
    expected = [overlay / name for name in file_names]
    missing = [path.name for path in expected if not path.is_file() or path.is_symlink()]
    discovered = sorted(overlay.glob(pattern))
    unexpected = [path.name for path in discovered if path not in set(expected)]
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing {label} files: {missing}")
        if unexpected:
            details.append(f"unexpected {label} files: {unexpected}")
        raise SystemExit(f"{label} layout mismatch: " + "; ".join(details))

    result: dict[str, dict[str, str]] = {}
    for path in expected:
        try:
            file_payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path.name}: invalid JSON: {exc}") from exc
        if not isinstance(file_payload, dict) or not set(file_payload).issubset(allowed_locales):
            raise SystemExit(f"{path.name}: invalid locales for {label}")
        for locale, entries in file_payload.items():
            if not isinstance(entries, dict) or any(
                not isinstance(key, str) or not isinstance(value, str) or not value.strip()
                for key, value in entries.items()
            ):
                raise SystemExit(f"{path.name}: invalid {label} entries for {locale}")
            target = result.setdefault(locale, {})
            duplicate = sorted(set(target) & set(entries))
            if duplicate:
                raise SystemExit(f"{path.name}: duplicate {label} keys: {duplicate[:8]}")
            target.update(entries)
    return result


def install_final_override_loader(core) -> None:
    original = core.load_override_bundle

    def load_override_bundle(overlay: pathlib.Path, overrides: dict[str, dict[str, str]]) -> None:
        base_payload = {locale: dict(entries) for locale, entries in overrides.items()}
        validate_reviewed_payload(
            "base reviewed override",
            base_payload,
            EXPECTED_BASE_COUNTS,
            EXPECTED_BASE_CANONICAL_SHA256,
        )

        original(overlay, overrides)

        final_payload = load_review_files(
            overlay,
            FINAL_OVERRIDE_FILES,
            "catalog-final-overrides.*.json",
            frozenset(core.RELEASE_CANDIDATES),
            "final semantic override",
        )
        validate_reviewed_payload(
            "final semantic override",
            final_payload,
            EXPECTED_FINAL_COUNTS,
            EXPECTED_FINAL_CANONICAL_SHA256,
        )

        visible_payload = {locale: dict(entries) for locale, entries in base_payload.items()}
        for locale, entries in final_payload.items():
            overlap = sorted(set(visible_payload[locale]) & set(entries))
            if overlap:
                raise SystemExit(
                    f"reviewed override layers overlap for {locale}: {overlap[:8]}"
                )
            visible_payload[locale].update(entries)
        validate_reviewed_payload(
            "combined source-visible override",
            visible_payload,
            EXPECTED_VISIBLE_COUNTS,
            EXPECTED_VISIBLE_CANONICAL_SHA256,
        )

        validate_reviewed_payload(
            "Portuguese UI override",
            PORTUGUESE_UI_OVERRIDES,
            EXPECTED_UI_COUNTS,
            EXPECTED_UI_CANONICAL_SHA256,
        )
        ui_overlap = sorted(
            set(visible_payload["pt"]) & set(PORTUGUESE_UI_OVERRIDES["pt"])
        )
        if ui_overlap:
            raise SystemExit(
                f"Portuguese UI override overlaps reviewed values: {ui_overlap[:8]}"
            )

        for locale, entries in final_payload.items():
            overrides.setdefault(locale, {}).update(entries)
        for locale, entries in PORTUGUESE_UI_OVERRIDES.items():
            overrides.setdefault(locale, {}).update(entries)

    core.load_override_bundle = load_override_bundle


def main() -> int:
    core = load_core()
    install_final_override_loader(core)
    return core.main()


if __name__ == "__main__":
    sys.exit(main())
