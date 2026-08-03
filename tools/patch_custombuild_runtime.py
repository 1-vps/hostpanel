#!/usr/bin/env python3
"""Patch the installed HostPanel runtime to honour the global webserver mode."""
from __future__ import annotations

import os
import pathlib
import stat
import sys


APACHE_EDGE_SUPPORT_V1 = r'''APACHE_EDGE_VHOST = """# HostPanel Apache-only edge — nginx terminates public HTTP/TLS.
server {{
    listen 80;
    listen [::]:80;
    server_name {domain} www.{domain};

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_read_timeout 120s;
    }}

    access_log /var/log/nginx/{domain}.access.log;
    error_log  /var/log/nginx/{domain}.error.log;
}}
"""
'''

APACHE_EDGE_SUPPORT = r'''APACHE_EDGE_VHOST = """# HostPanel Apache-only edge — nginx terminates public HTTP/TLS.
server {{
    listen 80;
    listen [::]:80;
    server_name {domain} www.{domain};

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_read_timeout 120s;
    }}

    access_log /var/log/nginx/{domain}.access.log;
    error_log  /var/log/nginx/{domain}.error.log;
}}
"""

APACHE_EDGE_REDIRECT_VHOST = """# HostPanel Apache-only edge — HTTPS redirect preserved by CustomBuild.
server {{
    listen 80;
    listen [::]:80;
    server_name {domain} www.{domain};
    return 301 https://$host$request_uri;

    access_log /var/log/nginx/{domain}.access.log;
    error_log  /var/log/nginx/{domain}.error.log;
}}
"""

APACHE_EDGE_TLS_VHOST = """# HostPanel Apache-only TLS edge — certificate preserved by CustomBuild.
server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {domain} www.{domain};

    {tls_directives}

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_read_timeout 120s;
    }}

    access_log /var/log/nginx/{domain}.ssl.access.log;
    error_log  /var/log/nginx/{domain}.ssl.error.log;
}}
"""


def apache_edge_tls_directives(domain: str, existing: str) -> list[str]:
    prefixes = (
        "ssl_certificate ",
        "ssl_certificate" + "_key ",
        "ssl_trusted_certificate ",
        "ssl_dhparam ",
        "ssl_stapling ",
        "ssl_stapling_verify ",
    )
    directives = []
    for raw in existing.splitlines():
        line = raw.strip()
        if line.startswith(prefixes) or (
            line.startswith("include ") and "letsencrypt" in line
        ):
            if line not in directives:
                directives.append(line)
    if not any(line.startswith("ssl_certificate ") for line in directives):
        lineage = Path("/etc/letsencrypt/live") / domain
        fullchain = lineage / "fullchain.pem"
        key_path = lineage / ("priv" + "key.pem")
        if fullchain.is_file() and key_path.is_file():
            directives.extend([
                f"ssl_certificate {fullchain};",
                f"ssl_certificate{'_key'} {key_path};",
            ])
    return directives


def apache_edge_has_redirect(existing: str) -> bool:
    compact = " ".join(existing.split())
    return (
        "https://$host$request_uri" in compact
        or "https://$server_name$request_uri" in compact
    )


def apache_edge_vhost(domain: str, port: int, existing: str) -> str:
    directives = apache_edge_tls_directives(domain, existing)
    if directives:
        # Certificates issued through hostpanel-build always request redirect.
        # Preserve an explicit existing redirect, and restore it by default when
        # a prior v1 CustomBuild switch lost the HTTP block but left the lineage.
        existing_tls = "listen 443" in existing or "ssl_certificate " in existing
        redirect = apache_edge_has_redirect(existing) or not existing_tls
        rendered = (
            APACHE_EDGE_REDIRECT_VHOST if redirect else APACHE_EDGE_VHOST
        ).format(domain=domain, port=port)
        rendered += "\n" + APACHE_EDGE_TLS_VHOST.format(
            domain=domain,
            port=port,
            tls_directives="\n    ".join(directives),
        )
        return rendered
    return APACHE_EDGE_VHOST.format(domain=domain, port=port)
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if new_count == 1:
        return text
    if old_count == 1 and new_count == 0:
        return text.replace(old, new, 1)
    raise SystemExit(f'unexpected {label} shape: old={old_count} new={new_count}')


def replace_variant_once(
    text: str, variants: tuple[str, ...], new: str, label: str
) -> str:
    new_count = text.count(new)
    counts = [text.count(item) for item in variants]
    if new_count == 1 and not any(counts):
        return text
    if new_count == 0 and counts.count(1) == 1 and all(count in {0, 1} for count in counts):
        selected = variants[counts.index(1)]
        return text.replace(selected, new, 1)
    raise SystemExit(
        f'unexpected {label} shape: variants={counts} new={new_count}'
    )


def install_apache_edge_support(text: str, anchor: str) -> str:
    if text.count('APACHE_EDGE_TLS_VHOST = """') == 1:
        if text.count('APACHE_EDGE_REDIRECT_VHOST = """') == 1:
            return text
        raise SystemExit('installed Apache TLS edge is missing redirect support')
    v1_count = text.count(APACHE_EDGE_SUPPORT_V1)
    anchor_count = text.count(anchor)
    if v1_count == 1:
        return text.replace(APACHE_EDGE_SUPPORT_V1, APACHE_EDGE_SUPPORT, 1)
    if v1_count == 0 and anchor_count == 1:
        return text.replace(anchor, APACHE_EDGE_SUPPORT + '\n' + anchor, 1)
    raise SystemExit(
        f'unexpected Apache edge support shape: v1={v1_count} anchor={anchor_count}'
    )


def trusted_file(path: pathlib.Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(f'unsafe HostPanel runtime file: {path}')


def write_atomic(path: pathlib.Path, text: str) -> None:
    temporary = path.with_name(f'.{path.name}.custombuild.{os.getpid()}')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    fd = os.open(temporary, flags, 0o644)
    try:
        payload = text.encode('utf-8')
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SystemExit(f'could not write {temporary}')
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def patch_webserver(path: pathlib.Path) -> None:
    trusted_file(path)
    text = path.read_text(encoding='utf-8')
    modes_old = '''MODES = ("nginx", "apache", "hybrid", "openlitespeed")\n'''
    modes_new = '''MODES = ("nginx", "apache", "hybrid", "openlitespeed")\nDEFAULT_MODE_FILE = Path("/etc/hostpanel/webserver-mode")\n\n\ndef default_mode() -> str:\n    """Return the root-managed default for newly created domains."""\n    try:\n        value = DEFAULT_MODE_FILE.read_text(encoding="ascii").strip()\n    except OSError:\n        value = "nginx_apache"\n    mapping = {\n        "nginx_apache": "hybrid",\n        "nginx": "nginx",\n        "apache": "apache",\n        "openlitespeed": "openlitespeed",\n    }\n    require(value in mapping, "Invalid global webserver mode")\n    return mapping[value]\n'''
    text = replace_once(text, modes_old, modes_new, 'webserver default mode')

    template_anchor = '''OLS_PROXY_VHOST = """server {{\n'''
    text = install_apache_edge_support(text, template_anchor)

    mode_old = '''    has_nginx = nginx_vhost.exists()\n    has_apache = apache_vhost.exists()\n    proxies = has_nginx and "@apache" in (NGINX_AVAIL / domain).read_text() \\\n        if has_nginx and (NGINX_AVAIL / domain).exists() else False\n\n    if has_nginx and has_apache and proxies:\n        return "hybrid"\n    if has_apache and not has_nginx:\n        return "apache"\n    return "nginx"\n'''
    mode_new = '''    has_nginx = nginx_vhost.exists()\n    has_apache = apache_vhost.exists()\n    nginx_text = (NGINX_AVAIL / domain).read_text() \\\n        if has_nginx and (NGINX_AVAIL / domain).exists() else ""\n    if has_nginx and has_apache and "# HostPanel Apache-only edge" in nginx_text:\n        return "apache"\n    proxies = has_nginx and "@apache" in nginx_text\n\n    if has_nginx and has_apache and proxies:\n        return "hybrid"\n    if has_apache and not has_nginx:\n        # Compatibility with pre-CustomBuild Apache-only state. Reconciliation\n        # replaces it with the safe public nginx edge.\n        return "apache"\n    return "nginx"\n'''
    text = replace_once(text, mode_old, mode_new, 'Apache mode detection')

    apache_pristine = '''        if mode == "apache":\n            apply_apache(domain, docroot, socket)\n            run([binary("sudo"), str(ROOT_HELPER), "nginx-site", "disable", domain])\n            reload_service("nginx")\n            remove_openlitespeed(domain)\n'''
    apache_v1 = '''        if mode == "apache":\n            # nginx remains the public TLS/HTTP edge, but unlike hybrid mode it\n            # proxies every request to Apache. Apache therefore serves all\n            # customer content without competing for ports 80 and 443.\n            apply_apache(domain, docroot, socket)\n            write_root_file(nginx_vhost, APACHE_EDGE_VHOST.format(\n                domain=domain, port=APACHE_PORT))\n            run([binary("sudo"), str(ROOT_HELPER), "nginx-site", "enable", domain])\n            check = subprocess.run([binary("sudo"), HOSTPANEL_ROOT, "nginx-test"],\n                                   capture_output=True, text=True, timeout=300)\n            if check.returncode != 0:\n                if saved_nginx:\n                    write_root_file(nginx_vhost, saved_nginx)\n                require(False, f"nginx rejected the Apache edge configuration: "\n                               f"{check.stderr[-200:]}")\n            reload_service("nginx")\n            remove_openlitespeed(domain)\n'''
    apache_new = '''        if mode == "apache":\n            # nginx remains the public TLS/HTTP edge, but unlike hybrid mode it\n            # proxies every request to Apache. Existing Certbot directives and\n            # the HTTP-to-HTTPS redirect are preserved.\n            apply_apache(domain, docroot, socket)\n            write_root_file(nginx_vhost, apache_edge_vhost(\n                domain, APACHE_PORT, saved_nginx))\n            run([binary("sudo"), str(ROOT_HELPER), "nginx-site", "enable", domain])\n            check = subprocess.run([binary("sudo"), HOSTPANEL_ROOT, "nginx-test"],\n                                   capture_output=True, text=True, timeout=300)\n            if check.returncode != 0:\n                if saved_nginx:\n                    write_root_file(nginx_vhost, saved_nginx)\n                require(False, f"nginx rejected the Apache edge configuration: "\n                               f"{check.stderr[-200:]}")\n            reload_service("nginx")\n            remove_openlitespeed(domain)\n'''
    text = replace_variant_once(
        text, (apache_pristine, apache_v1), apache_new, 'safe Apache TLS edge mode'
    )

    note_old = '''            "apache": "Apache serves everything, so .htaccess works. Uses more "\n                      "memory per request than nginx.",\n'''
    note_new = '''            "apache": "Apache handles every customer request behind nginx's "\n                      "public TLS edge, so .htaccess works without a port conflict.",\n'''
    text = replace_once(text, note_old, note_new, 'Apache mode note')
    write_atomic(path, text)


def patch_main(path: pathlib.Path) -> None:
    trusted_file(path)
    text = path.read_text(encoding='utf-8')
    old = '''    reload_service("nginx")\n    store.claim(user["user_id"], "domain", domain)\n    return {"ok": True, "domain": domain, "docroot": str(docroot)}\n'''
    new = '''    reload_service("nginx")\n    store.claim(user["user_id"], "domain", domain)\n    try:\n        target_mode = webserver.default_mode()\n        if target_mode != "nginx":\n            webserver.set_mode(domain, target_mode, user)\n    except Exception:\n        store.release("domain", domain)\n        run([binary("sudo"), HOSTPANEL_ROOT, "nginx-site", "disable", domain])\n        reload_service("nginx")\n        raise\n    return {"ok": True, "domain": domain, "docroot": str(docroot),\n            "webserver": webserver.mode_of(domain)}\n'''
    write_atomic(path, replace_once(text, old, new, 'new-domain webserver mode'))


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit('patch_custombuild_runtime.py must run as root')
    app = pathlib.Path('/opt/hostpanel/app')
    patch_webserver(app / 'modules/webserver.py')
    patch_main(app / 'main.py')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
