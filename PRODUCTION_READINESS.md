# HostPanel production readiness

This checklist applies to `3.4.0-hardened-r6` after the installer pipeline has
passed. It does not replace validation on a real systemd VM using the target
kernel, firewall, storage, DNS, and mail environment.

Validated installer integration commit:

```text
4e8393696bae3baa41e4fdb1b57307a2a126488f
```

## Prepare a disposable VM

Use a fresh VM of the exact target operating system. Keep provider-console access
available and create a provider-level snapshot before installation. Do not begin
with a production server or a host containing customer data.

Record the operating-system image, kernel version, public and private addresses,
SSH port, panel hostname, administrative CIDR, customer-data filesystem, and
selected HostPanel roles.

## Install from the pinned commit

```bash
REVIEWED_COMMIT_SHA=4e8393696bae3baa41e4fdb1b57307a2a126488f

sudo curl -fsSL \
  "https://raw.githubusercontent.com/1-vps/hostpanel/${REVIEWED_COMMIT_SHA}/bootstrap-install.sh" \
  -o /root/bootstrap-install.sh
sudo chmod 700 /root/bootstrap-install.sh
sudo bash -n /root/bootstrap-install.sh

sudo env \
  HP_REPO_REF="$REVIEWED_COMMIT_SHA" \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_MULTI_PHP_REPO=off \
  HP_RSPAMD_REPO=off \
  bash /root/bootstrap-install.sh --check --mta postfix

sudo env \
  HP_REPO_REF="$REVIEWED_COMMIT_SHA" \
  HP_PANEL_HOST=panel.example.com \
  HP_PANEL_ADMIN_CIDR=192.0.2.10/32 \
  HP_MULTI_PHP_REPO=off \
  HP_RSPAMD_REPO=off \
  bash /root/bootstrap-install.sh --mta postfix
```

Preserve `/var/log/hostpanel-install.log` and the installer snapshot path printed
by the run.

## Download the VM validator

```bash
sudo curl -fsSL \
  "https://raw.githubusercontent.com/1-vps/hostpanel/${REVIEWED_COMMIT_SHA}/tools/validate-production-vm.sh" \
  -o /root/validate-production-vm.sh
sudo chmod 700 /root/validate-production-vm.sh
sudo bash -n /root/validate-production-vm.sh
```

The validator is read-only unless separately reviewed operator hook scripts are
explicitly supplied. Its normal checks cover the installed version, systemd
units, failed services, application doctor, service configuration, listeners,
firewall state, Redis ACL policy, mail listeners, file permissions, DNS
resolution, and certificate validity.

## Initial validation

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.0-hardened-r6 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --check
```

Resolve every failure before continuing. Warnings require review but do not make
the script fail automatically.

## Verify reboot persistence

Record the current boot ID only after the initial checks pass:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.0-hardened-r6 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --prepare-reboot

sudo reboot
```

Reconnect over the configured SSH port and run:

```bash
sudo env \
  HP_EXPECTED_VERSION=3.4.0-hardened-r6 \
  HP_PANEL_HOST=panel.example.com \
  HP_EXPECTED_PUBLIC_IP=192.0.2.20 \
  bash /root/validate-production-vm.sh --post-reboot
```

The post-reboot mode requires a changed kernel boot ID before it accepts the
reboot check.

## End-to-end role tests

The validator confirms local state. Production acceptance also requires tests
from a separate network:

- panel login and session handling over trusted TLS
- customer website creation, TLS issuance, PHP execution, and log access
- database creation, authentication, backup, and restore
- authoritative DNS delegation and external query resolution
- inbound and outbound mail, submission, IMAP, SPF, DKIM, DMARC, and reverse DNS
- scheduled backup completion and restoration into a disposable test account
- quota enforcement on the actual customer-data filesystem
- reconnecting over SSH after firewall reload and reboot

Do not treat local port checks as proof of external DNS delegation or mail
deliverability.

## Destructive recovery tests

Restore and failure-injection tests must use a disposable VM, a confirmed
provider snapshot, and separately reviewed root-owned scripts. The validator
will not invent shell commands or execute arbitrary command strings. See its
`--help` output for the accepted script-path environment variables and safety
gates.

## Acceptance evidence

Retain the installer and validator logs, exact Git commit, source archive
checksum, VM image and kernel, boot IDs, doctor output, service and firewall
status, external web/DNS/mail results, backup and restore evidence, rollback
evidence, and final provider snapshot identifier.

A release is not production-ready until every selected role passes on the exact
operating system and infrastructure intended for deployment.
