#!/usr/bin/env python3
"""Translate short English labels left unchanged by the Japanese MT model."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

from deep_translator import GoogleTranslator

BRANDS = {
    "HostPanel", "PostgreSQL", "MariaDB", "MySQL", "SQLite", "Redis",
    "WordPress", "WooCommerce", "Joomla", "Laravel", "Nginx", "Apache",
    "OpenLiteSpeed", "LiteSpeed", "Docker", "Podman", "Git", "GitHub",
    "Cloudflare", "PowerDNS", "Hetzner", "DigitalOcean", "Vultr", "Linode",
    "Akamai", "AWS", "Azure", "EC2", "Exim", "Postfix", "Dovecot",
    "Rspamd", "Roundcube", "WebAuthn", "WebDAV", "CalDAV", "CardDAV",
}
ACRONYM_RE = re.compile(r"^[A-Z0-9][A-Z0-9+._-]{1,15}$")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
CODE_RE = re.compile(
    r"(?:https?://|/|@|\{|\}|`|--[A-Za-z]|^[a-z0-9_.:-]+(?:,[a-z0-9_.:-]+)+$|"
    r"\.(?:json|ya?ml|toml|ini|conf|pem|key|crt|csr|sql|csv|tsv|txt|log|zip|tar|gz|php|py|js|css|html?)$)",
    re.I,
)

MANUAL = {
    "Cancel": "キャンセル", "Close": "閉じる", "Continue": "続行",
    "Information": "情報", "Value": "値", "Account": "アカウント",
    "Accounts": "アカウント", "Action": "操作", "Actions": "操作",
    "Active": "有効", "Add": "追加", "Address": "アドレス",
    "Addresses": "アドレス", "Alert": "アラート", "Alerts": "アラート",
    "Application": "アプリケーション", "Applications": "アプリケーション",
    "Apply": "適用", "Archive": "アーカイブ", "Assign": "割り当て",
    "Backups": "バックアップ", "Bandwidth": "帯域幅", "Billing": "請求",
    "Block": "ブロック", "Branch": "ブランチ", "Build": "ビルド",
    "Cache": "キャッシュ", "Certificates": "証明書", "Checks": "チェック",
    "Code": "コード", "Command": "コマンド", "Comment": "コメント",
    "Company": "会社", "Condition": "条件", "Contents": "内容",
    "Create": "作成", "Created": "作成済み", "Current": "現在",
    "Daily": "毎日", "Dashboard": "ダッシュボード", "Database": "データベース",
    "Databases": "データベース", "Deploy": "デプロイ", "Description": "説明",
    "Destination": "宛先", "Detected": "検出済み", "Directory": "ディレクトリ",
    "Disable": "無効化", "Disk": "ディスク", "Domain": "ドメイン",
    "Domains": "ドメイン", "Email": "メール", "Enable": "有効化",
    "Enabled": "有効", "Error": "エラー", "Errors": "エラー",
    "Export": "エクスポート", "Failed": "失敗", "File": "ファイル",
    "Files": "ファイル", "Firewall": "ファイアウォール", "Finished": "完了",
    "Global": "グローバル", "Group": "グループ", "Groups": "グループ",
    "Import": "インポート", "Inactive": "無効", "Install": "インストール",
    "Language": "言語", "Logs": "ログ", "Maintenance": "メンテナンス",
    "Memory": "メモリ", "Migration": "移行", "Never": "なし",
    "Node": "ノード", "Nodes": "ノード", "Optional": "任意",
    "Password": "パスワード", "Pause": "一時停止", "Plan": "プラン",
    "Plans": "プラン", "Policy": "ポリシー", "Provider": "プロバイダー",
    "Queue": "キュー", "Queued": "キュー待ち", "Remove": "削除",
    "Removed": "削除済み", "Restart": "再起動", "Restore": "復元",
    "Resume": "再開", "Role": "ロール", "Roles": "ロール",
    "Run": "実行", "Save": "保存", "Saved": "保存済み", "Search": "検索",
    "Security": "セキュリティ", "Server": "サーバー", "Settings": "設定",
    "Status": "状態", "Storage": "ストレージ", "Suspend": "停止",
    "System": "システム", "Target": "対象", "Tasks": "タスク",
    "Token": "トークン", "Tokens": "トークン", "Update": "更新",
    "Upload": "アップロード", "Uploaded": "アップロード済み", "User": "ユーザー",
    "Users": "ユーザー", "Username": "ユーザー名", "Verified": "検証済み",
    "Version": "バージョン", "Website": "ウェブサイト", "Websites": "ウェブサイト",
    "Yes": "はい", "No": "いいえ",
}


def candidate(value: str) -> bool:
    text = value.strip()
    if not text or text in BRANDS or CODE_RE.search(text):
        return False
    if ACRONYM_RE.fullmatch(text):
        return False
    words = WORD_RE.findall(text)
    return 1 <= len(words) <= 6 and len(text) <= 100


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("english", type=pathlib.Path)
    parser.add_argument("japanese", type=pathlib.Path)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    args = parser.parse_args()

    english = json.loads(args.english.read_text(encoding="utf-8"))
    japanese = json.loads(args.japanese.read_text(encoding="utf-8"))
    values = sorted({source for key, source in english.items() if japanese.get(key) == source and candidate(source)})
    translator = GoogleTranslator(source="en", target="ja")
    translations: dict[str, str] = dict(MANUAL)
    failures: list[dict[str, str]] = []

    for index, source in enumerate(values, start=1):
        if source in translations:
            continue
        error = ""
        for attempt in range(4):
            try:
                result = translator.translate(source)
                if result and result.strip() and result.strip() != source:
                    translations[source] = result.strip()
                    break
                error = "empty or source-identical result"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.4 * (attempt + 1))
        else:
            failures.append({"source": source, "error": error})
        if index % 25 == 0:
            print(f"ja-labels: {index}/{len(values)}", flush=True)
        time.sleep(0.08)

    updated = 0
    for key, source in english.items():
        if japanese.get(key) == source and source in translations:
            japanese[key] = translations[source]
            updated += 1

    args.japanese.write_text(json.dumps(japanese, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    remaining = sorted({source for key, source in english.items() if japanese.get(key) == source and candidate(source)})
    report = {
        "candidate_values": len(values), "updated_keys": updated,
        "failed_values": failures, "remaining_candidate_values": remaining,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if len(remaining) > 20:
        raise SystemExit(f"too many untranslated Japanese labels remain: {len(remaining)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
