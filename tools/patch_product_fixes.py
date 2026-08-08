#!/usr/bin/env python3
"""Apply reviewed security, performance, and role-aware UI fixes.

The signed source release remains immutable.  This patcher is installed from the
commit-addressed overlay and fails closed if the reviewed source shape changes.
"""
from __future__ import annotations

import os
import pathlib
import stat
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if new_count == 1:
        return text
    if old_count == 1 and new_count == 0:
        return text.replace(old, new, 1)
    raise SystemExit(
        f"unexpected {label} source shape: old={old_count} new={new_count}"
    )


def patch_mail(text: str) -> str:
    old = '''@router.get("/queue")
def queue(user: dict = Depends(current_user)):
    """How many messages are waiting to go out — the first thing to check."""
    output = mail_backend.queue_text()
    messages = mail_backend.queue_messages()
    empty = not messages
    return {"count": len(messages), "detail": "Mail queue is empty" if empty else output[-2000:],
            "mta": mail_backend.current_mta()}
'''
    new = '''@router.get("/queue")
def queue(user: dict = Depends(current_user)):
    """Server-wide queue metadata is restricted to administrators."""
    require(is_admin(user), "Only administrators can inspect the server mail queue")
    output = mail_backend.queue_text()
    messages = mail_backend.queue_messages()
    empty = not messages
    return {"count": len(messages), "detail": "Mail queue is empty" if empty else output[-2000:],
            "mta": mail_backend.current_mta()}
'''
    return replace_once(text, old, new, "mail queue authorization")


def patch_accounts(text: str) -> str:
    text = replace_once(
        text,
        "def describe(user: dict) -> dict:\n",
        "def describe(user: dict, *, include_disk: bool = False) -> dict:\n",
        "account serializer signature",
    )
    text = replace_once(
        text,
        '        "disk_bytes": directory_size(home),\n',
        '        "disk_bytes": directory_size(home) if include_disk else None,\n',
        "bounded account disk usage",
    )
    text = replace_once(
        text,
        "    return describe(account)\n",
        "    return describe(account, include_disk=True)\n",
        "self account disk usage",
    )
    return text


def patch_panel_script(text: str) -> str:
    old_dashboard = '''async function loadDashboard(){
  clearPageError();
  try{await loadStats();}catch(e){}
  try{
    const {services} = await api('/api/services');
    $('#svcBody').innerHTML = services.map(s => `
      <tr><td class="mono">${esc(s.name)}</td>
      <td><span class="tag ${s.running?'ok':'bad'}">${esc(s.state)}</span></td>
      <td class="right"><button class="btn sm" data-hp-click="dc001" data-hp-a0="${esc(s.name)}">Restart</button></td></tr>`).join('');
  }catch(e){ $('#svcBody').innerHTML = `<tr><td colspan="3" class="empty">${esc(e.message)}</td></tr>`; }
  try{
    const q = await api('/api/mail/queue');
    $('#mq').textContent = q.count;
  }catch(e){ $('#mq').textContent = '—'; }
}
'''
    new_dashboard = '''async function loadOperationalStatus(){
  if(ROLE!=='admin') return;
  try{
    const {services} = await api('/api/services');
    $('#svcBody').innerHTML = services.map(s => `
      <tr><td class="mono">${esc(s.name)}</td>
      <td><span class="tag ${s.running?'ok':'bad'}">${esc(s.state)}</span></td>
      <td class="right"><button class="btn sm" data-hp-click="dc001" data-hp-a0="${esc(s.name)}">Restart</button></td></tr>`).join('');
  }catch(e){ $('#svcBody').innerHTML = `<tr><td colspan="3" class="empty">${esc(e.message)}</td></tr>`; }
  try{
    const q = await api('/api/mail/queue');
    $('#mq').textContent = q.count;
  }catch(e){ $('#mq').textContent = '—'; }
}
async function loadDashboard(){
  clearPageError();
  try{await loadStats();}catch(e){}
  await loadOperationalStatus();
}
'''
    text = replace_once(text, old_dashboard, new_dashboard, "role-aware dashboard loading")
    text = replace_once(
        text,
        "${bytes(u.disk_bytes)}${u.limits.disk_mb?` / ${u.limits.disk_mb} MB`:''}",
        "${u.disk_bytes===null?'Not scanned':bytes(u.disk_bytes)}${u.limits.disk_mb?` / ${u.limits.disk_mb} MB`:''}",
        "account disk usage rendering",
    )
    text = replace_once(
        text,
        "setInterval(()=>{if(currentPage==='dashboard')loadStats().catch(()=>{});},5000);",
        "setInterval(()=>{if(currentPage==='dashboard')loadStats().catch(()=>{});},5000);\nsetInterval(()=>{if(currentPage==='dashboard'&&ROLE==='admin')loadOperationalStatus().catch(()=>{});},10000);",
        "dashboard operational refresh",
    )
    return text


def patch_browser_server(text: str) -> str:
    return replace_once(
        text,
        'store.init("admin", "browser-password-1234")\nstore.migrate()\n',
        'store.init("admin", "browser-password-1234")\nstore.migrate()\n'
        'if store.get_user("browseruser") is None:\n'
        '    store.create_user("browseruser", "browser-user-password-1234", "user", '
        '"browseruser@example.test")\n',
        "browser tenant seed",
    )


PATCHERS = {
    "app/modules/mail.py": patch_mail,
    "app/modules/accounts.py": patch_accounts,
    "app/static/panel.js": patch_panel_script,
    "tests/browser/run_server.py": patch_browser_server,
}


def write_atomic(path: pathlib.Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = path.with_name(f".{path.name}.product-fixes.{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_product_fixes.py SOURCE_ROOT")
    root = pathlib.Path(sys.argv[1])
    if not root.is_dir() or root.is_symlink():
        raise SystemExit(f"unsafe source root: {root}")
    for relative, patcher in PATCHERS.items():
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"unsafe product patch target: {path}")
        original = path.read_text(encoding="utf-8")
        updated = patcher(original)
        if updated != original:
            write_atomic(path, updated)


if __name__ == "__main__":
    main()
