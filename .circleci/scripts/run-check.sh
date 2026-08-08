#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
unset ENV PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH

root="$(git rev-parse --show-toplevel)"
cd "$root"
.circleci/scripts/verify-build.sh

if (($# != 0)); then
  printf 'CircleCI validation failed: positional arguments are forbidden\n' >&2
  exit 1
fi

die() {
  printf 'CircleCI validation failed: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

check="${HP_CIRCLECI_CHECK:-}"
case "$check" in
  repository)
    require_command python3
    python3 -m compileall -q tools tests localization-overlay
    python3 -m unittest discover -s tests -p 'test_*.py' -v
    ;;
  shell)
    require_command python3
    require_command bash
    require_command shellcheck
    python3 tools/harden_install.py install.base.sh /tmp/install.generated.sh
    shell_paths=(
      install.sh bootstrap-install.sh quick-install.sh auto-install.sh
      install-one-line.sh test-matrix.sh run-full-test-matrix.sh
      tools/install-hostpanel-build.sh tools/validate-production-vm.sh
      tools/run-qemu-vm-acceptance.sh tools/qemu-guest-install.sh
      tools/install-update-agent.sh /tmp/install.generated.sh
    )
    existing=()
    for path in "${shell_paths[@]}"; do
      [[ -f "$path" ]] || continue
      bash -n "$path"
      existing+=("$path")
    done
    ((${#existing[@]} > 0)) || die "no shell paths were found"
    shellcheck -S error "${existing[@]}"
    ;;
  metadata)
    require_command python3
    if [[ -f RELEASE-MANIFEST.json && -f tools/validate_release_manifest.py ]]; then
      python3 tools/validate_release_manifest.py
      [[ ! -f tests/test_release_manifest.py ]] || python3 -m unittest -v tests.test_release_manifest
    else
      printf 'RELEASE-MANIFEST.json is not present on this branch; manifest validation skipped.\n'
    fi
    python3 -m py_compile tools/release_legal.py
    ;;
  production-validator)
    require_command python3
    require_command bash
    require_command shellcheck
    bash -n tools/validate-production-vm.sh
    python3 -m unittest -v tests.test_production_vm_validation
    shellcheck -S error tools/validate-production-vm.sh
    ;;
  installer-os)
    os_name="${HP_CIRCLECI_OS:-}"
    [[ "$os_name" =~ ^(ubuntu-(22\.04|24\.04|26\.04)|debian-(12|13)|rocky-(9|10)|almalinux-(9|10))$ ]] || {
      die "unsupported installer matrix target: ${os_name:-<empty>}"
    }
    require_command docker
    ./test-matrix.sh --os "$os_name"
    ;;
  qemu-contracts)
    require_command python3
    require_command bash
    bash -n tools/run-qemu-vm-acceptance.sh
    bash -n tools/qemu-guest-install.sh
    bash -n tools/install-update-agent.sh
    python3 -m py_compile \
      tools/prepare-qemu-evidence.py tools/sanitize-qemu-evidence.py \
      tools/seal-qemu-evidence.py tools/build-update-release.py \
      tools/hostpanel-update.py
    for test_file in \
      test_qemu_vm_acceptance.py test_qemu_token_scope.py \
      test_qemu_evidence_sanitizer.py test_qemu_early_evidence.py \
      test_qemu_evidence_sealer.py test_qemu_default_version.py \
      test_qemu_reboot_wait.py test_systemd_resolved_dns.py \
      test_post_install_health.py test_first_reboot_hardening.py
    do
      [[ -f "tests/$test_file" ]] || continue
      python3 -m unittest discover -s tests -p "$test_file" -v
    done
    ;;
  *) die "unknown or missing HP_CIRCLECI_CHECK: ${check:-<empty>}" ;;
esac
