#!/usr/bin/env python3
"""Apply the reviewed HostPanel control-center UI overlay to panel.html."""
from __future__ import annotations

import os
import pathlib
import re
import stat
import sys

STYLE_TAG = '<link href="/static/panel-redesign.css" rel="stylesheet"/>'
SCRIPT_TAG = '<script defer="" src="/static/panel-redesign.js"></script>'
DASHBOARD_MARKER = 'class="page hp-dashboard" data-view="dashboard"'
SECTION_TAG = re.compile(r"</?section\b[^>]*>", re.IGNORECASE)

DASHBOARD = r'''<section class="page hp-dashboard" data-view="dashboard">
<div class="page-h hp-dashboard-hero">
<div class="hp-dashboard-hero-copy"><span class="hp-dashboard-kicker" data-i18n="ui.overview">Overview</span><h2 data-i18n="ui.dashboard.4">Dashboard</h2><p data-i18n="ui.live.metrics.from.this.server.refreshed.every.few.seconds">Live metrics from this server, refreshed every few seconds.</p></div>
<div class="hp-dashboard-toolbar"><div aria-live="polite" class="dashboard-state" id="dashboardState" role="status"><span data-i18n="ui.waiting.for.first.update" id="dashboardUpdated">Waiting for first update…</span><span class="error" hidden="" id="dashboardError"></span><button class="btn sm" data-i18n="ui.refresh" id="dashboardRetry" type="button">Refresh</button></div></div>
</div><div class="request-error page-error-summary" role="alert" tabindex="-1"></div>
<div class="hp-dashboard-layout">
<div class="hp-dashboard-main">
<div class="hp-metric-grid">
<div class="card hp-metric-card" data-tone="blue"><div class="hp-metric-top"><div class="hp-metric-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 14a8 8 0 1 1 16 0"></path><path d="m12 14 4-4"></path><path d="M5 18h14"></path></svg></div><div class="gl" data-i18n="ui.cpu.load">CPU load</div></div><div><div class="loading-skeleton gv" id="cpu">—</div><div class="gd" id="cpud">—</div><div class="hp-meter" aria-hidden="true"><span id="cpuMeter"></span></div></div></div>
<div class="card hp-metric-card" data-tone="green"><div class="hp-metric-top"><div class="hp-metric-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="5" y="5" width="14" height="14" rx="2"></rect><path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"></path></svg></div><div class="gl" data-i18n="ui.memory">Memory</div></div><div><div class="loading-skeleton gv" id="ram">—</div><div class="gd" id="ramd">—</div><div class="hp-meter" aria-hidden="true"><span id="ramMeter"></span></div></div></div>
<div class="card hp-metric-card" data-tone="violet"><div class="hp-metric-top"><div class="hp-metric-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="8" ry="3"></ellipse><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"></path></svg></div><div class="gl" data-i18n="ui.disk">Disk</div></div><div><div class="loading-skeleton gv" id="disk">—</div><div class="gd" id="diskd">—</div><div class="hp-meter" aria-hidden="true"><span id="diskMeter"></span></div></div></div>
<div class="card hp-metric-card" data-tone="amber"><div class="hp-metric-top"><div class="hp-metric-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 18V9M10 18V5M16 18v-7M22 18V2"></path></svg></div><div class="gl" data-i18n="ui.load.average">Load average</div></div><div><div class="loading-skeleton gv" id="loadv">—</div><div class="gd" data-i18n="ui.1.5.15.min">1 / 5 / 15 min</div><div class="hp-meter hp-meter-static" aria-hidden="true"><span></span></div></div></div>
</div>
<div class="hp-dashboard-split">
<section class="card hp-panel-card" aria-labelledby="hpHealthTitle"><div class="hp-card-head"><div><h3 data-i18n="ui.services" id="hpHealthTitle">Services</h3><p data-i18n="ui.live.metrics.from.this.server.refreshed.every.few.seconds">Live metrics from this server, refreshed every few seconds.</p></div><span class="hp-service-summary" id="hpServiceSummary">Waiting for services</span></div><div class="hp-health-body"><div class="hp-health-ring" id="hpHealthRing"><div class="hp-health-value"><strong id="hpHealthPercent">—</strong><span data-i18n="ui.running">running</span></div></div><div class="hp-health-list"><div class="hp-health-row"><span class="hp-health-label"><i class="hp-health-dot"></i><span data-i18n="ui.services">Services</span></span><strong class="hp-health-meta" id="hpServiceCount">—</strong></div><div class="hp-health-row"><span class="hp-health-label"><i class="hp-health-dot"></i><span data-i18n="ui.uptime">uptime —</span></span><strong class="hp-health-meta" id="dashboardUptime">—</strong></div><div class="hp-health-row"><span class="hp-health-label"><i class="hp-health-dot"></i><span data-i18n="ui.security">Security</span></span><button class="btn sm" data-hp-page="security" type="button">Open</button></div></div></div></section>
<section class="card hp-panel-card" aria-labelledby="hpActionsTitle"><div class="hp-card-head"><div><h3 data-i18n="ui.actions" id="hpActionsTitle">Actions</h3><p data-i18n="ui.filter.menu.ctrl.k">Search tools and pages…  Ctrl+K</p></div></div><div class="hp-quick-grid"><button class="hp-quick-action" data-hp-page="domains" type="button"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"></circle><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"></path></svg><span data-i18n="ui.domains">Domains</span></button><button class="hp-quick-action" data-hp-page="mail" type="button"><svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="m4 7 8 6 8-6"></path></svg><span data-i18n="ui.mailboxes">Mailboxes</span></button><button class="hp-quick-action" data-hp-page="databases" type="button"><svg aria-hidden="true" viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="8" ry="3"></ellipse><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"></path></svg><span data-i18n="ui.databases">Databases</span></button><button class="hp-quick-action" data-hp-page="backups" type="button"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16v13H4zM8 7V4h8v3M9 12h6M9 16h4"></path></svg><span data-i18n="ui.backups">Backups</span></button><button class="hp-quick-action" data-hp-page="certs" type="button"><svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="9" cy="12" r="5"></circle><path d="M14 12h7M18 12v4M21 12v3"></path></svg><span data-i18n="ui.certificates">Certificates</span></button><button class="hp-quick-action" data-hp-page="security" type="button"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3 20 6v6c0 5-3 8-8 10-5-2-8-5-8-10V6z"></path><path d="m8.5 12 2.2 2.2 4.8-5"></path></svg><span data-i18n="ui.security">Security</span></button></div></section>
</div>
<section class="card hp-panel-card hp-services-card" aria-labelledby="hpServicesTitle"><div class="hp-card-head"><div><h3 data-i18n="ui.services" id="hpServicesTitle">Services</h3><p data-i18n="ui.live.metrics.from.this.server.refreshed.every.few.seconds">Live metrics from this server, refreshed every few seconds.</p></div><button class="btn sm" data-hp-page="directadmin" type="button" data-i18n="ui.server.operations">Server operations</button></div><div aria-label="Dashboard table" class="table-wrap" data-i18n-aria-label="ui.dashboard.table.2" role="region" tabindex="0"><table><caption class="visually-hidden-caption" data-i18n="ui.dashboard.table">Dashboard table</caption><thead><tr><th data-i18n="ui.service" scope="col">Service</th><th data-i18n="ui.state" scope="col">State</th><th scope="col"></th></tr></thead><tbody id="svcBody"><tr><td class="empty" colspan="3" data-i18n="ui.loading">Loading…</td></tr></tbody></table></div><div aria-hidden="true" class="table-scroll-hint" data-i18n="ui.scroll.horizontally.to.view.more.columns">Scroll horizontally to view more columns.</div></section>
</div>
<aside class="hp-dashboard-rail" aria-label="Dashboard overview">
<section class="card hp-rail-card"><h3 data-i18n="ui.mail.queue">Mail queue</h3><div class="hp-mail-count" id="mq">—</div><p class="hp-mail-copy" data-i18n="ui.messages.waiting.to.be.delivered">messages waiting to be delivered</p><div class="hp-mail-state" id="hpMailState">Delivery queue healthy</div></section>
<section class="card hp-rail-card"><h3 data-i18n="ui.overview">Overview</h3><div class="hp-overview-list"><div class="hp-overview-row"><span data-i18n="ui.uptime">uptime —</span><strong id="dashboardUptimeRail">Live</strong></div><div class="hp-overview-row"><span data-i18n="ui.server">server /</span><strong>HostPanel</strong></div><div class="hp-overview-row"><span data-i18n="ui.dashboard.4">Dashboard</span><strong>3.0</strong></div></div></section>
<section class="card hp-rail-card hp-security-card"><h3 data-i18n="ui.security">Security</h3><div class="hp-security-links"><a class="hp-security-link" data-hp-page="security" href="#/panel/security" data-i18n="ui.server.security">Server security</a><a class="hp-security-link" data-hp-page="firewall" href="#/panel/firewall" data-i18n="ui.firewall">Firewall</a><a class="hp-security-link" data-hp-page="certs" href="#/panel/certs" data-i18n="ui.certificates">Certificates</a><a class="hp-security-link" data-hp-page="backups" href="#/panel/backups" data-i18n="ui.backups.recovery">Backups &amp; recovery</a></div></section>
</aside>
</div>
</section>'''


def _insert_once(text: str, anchor: str, insertion: str, label: str) -> str:
    if insertion in text:
        return text
    if text.count(anchor) != 1:
        raise SystemExit(f"unexpected {label} anchor count: {text.count(anchor)}")
    return text.replace(anchor, f"{anchor}\n{insertion}", 1)


def _place_before_once(text: str, anchor: str, insertion: str, label: str) -> str:
    if text.count(insertion) > 1:
        raise SystemExit(f"unexpected {label} count: {text.count(insertion)}")
    if insertion in text:
        text = text.replace(f"{insertion}\n", "", 1)
        if insertion in text:
            text = text.replace(insertion, "", 1)
    if text.count(anchor) != 1:
        raise SystemExit(f"unexpected {label} anchor count: {text.count(anchor)}")
    return text.replace(anchor, f"{insertion}\n{anchor}", 1)


def _balanced_section_end(text: str, start: int) -> int:
    depth = 0
    first = True
    for match in SECTION_TAG.finditer(text, start):
        if first:
            first = False
            if match.start() != start or match.group(0).startswith("</"):
                raise SystemExit("reviewed dashboard section does not start with an opening section tag")
        if match.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return match.end()
            if depth < 0:
                break
        else:
            depth += 1
    raise SystemExit("reviewed dashboard section tags are unbalanced")


def patch(text: str) -> str:
    text = _insert_once(
        text,
        '<link href="/static/panel.css" rel="stylesheet"/>',
        STYLE_TAG,
        "redesign stylesheet",
    )
    text = _place_before_once(
        text,
        '<script defer="" src="/static/panel-i18n.js"></script>',
        SCRIPT_TAG,
        "redesign script",
    )

    if DASHBOARD_MARKER not in text:
        start_marker = '<section class="page" data-view="dashboard">'
        start = text.find(start_marker)
        if start < 0:
            raise SystemExit("could not locate the reviewed dashboard section")
        end = _balanced_section_end(text, start)
        original = text[start:end]
        required_ids = ('id="cpu"', 'id="ram"', 'id="disk"', 'id="loadv"', 'id="svcBody"', 'id="mq"')
        if not all(marker in original for marker in required_ids):
            raise SystemExit("reviewed dashboard contract changed unexpectedly")
        text = text[:start] + DASHBOARD + text[end:]

    old_body = 'data-ui-version="2.0.0"'
    new_body = 'data-ui-version="3.0.0"'
    if old_body in text and new_body not in text:
        if text.count(old_body) != 1:
            raise SystemExit("unexpected panel UI version marker count")
        text = text.replace(old_body, new_body, 1)
    elif new_body not in text:
        raise SystemExit("panel UI version marker is missing")

    old_theme = '<meta content="#0F766E" name="theme-color"/>'
    new_theme = '<meta content="#2563EB" name="theme-color"/>'
    if old_theme in text and new_theme not in text:
        if text.count(old_theme) != 1:
            raise SystemExit("unexpected theme-color marker count")
        text = text.replace(old_theme, new_theme, 1)
    elif new_theme not in text:
        raise SystemExit("theme-color marker is missing")
    return text


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_panel_ui.py PANEL_TEMPLATE")
    path = pathlib.Path(sys.argv[1])
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"unsafe panel template target: {path}")
    original = path.read_text(encoding="utf-8")
    updated = patch(original)
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = path.with_name(f".{path.name}.ui-overlay.{os.getpid()}")
    try:
        temporary.write_text(updated, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
