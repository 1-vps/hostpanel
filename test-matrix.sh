#!/usr/bin/env bash
#
# HostPanel Installer Test Matrix - All Supported Operating Systems
# Tests install.sh across Ubuntu 22.04/24.04/26.04, Debian 12/13, Rocky 9/10, AlmaLinux 9/10
#
# Usage:
#   ./test-matrix.sh [--check-only] [--verbose] [--os OSNAME]
#
# Supported OS names:
#   ubuntu-22.04, ubuntu-24.04, ubuntu-26.04, debian-12, debian-13
#   rocky-9, rocky-10, almalinux-9, almalinux-10
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/test-logs"
REPORT_FILE="${LOG_DIR}/test-report.md"
CHECK_ONLY=0
VERBOSE=0
TARGET_OS=""

# Test matrix definitions
declare -A OS_IMAGES=(
  [ubuntu-22.04]="ubuntu:22.04"
  [ubuntu-24.04]="ubuntu:24.04"
  [ubuntu-26.04]="ubuntu:26.04"
  [debian-12]="debian:12"
  [debian-13]="debian:13"
  [rocky-9]="rockylinux/rockylinux:9"
  [rocky-10]="rockylinux/rockylinux:10"
  [almalinux-9]="almalinux:9"
  [almalinux-10]="almalinux:10"
)

declare -A PKG_MANAGERS=(
  [ubuntu-22.04]="debian"
  [ubuntu-24.04]="debian"
  [ubuntu-26.04]="debian"
  [debian-12]="debian"
  [debian-13]="debian"
  [rocky-9]="rhel"
  [rocky-10]="rhel"
  [almalinux-9]="rhel"
  [almalinux-10]="rhel"
)

PREFLIGHT_COMMANDS=(bash python3 awk df grep hostname tail)
declare -A PREFLIGHT_PACKAGES=(
  [debian]="bash python3 ca-certificates coreutils grep gawk hostname"
  [rhel]="bash python3 ca-certificates coreutils-single grep gawk hostname"
)

###############################################################################
# UTILITY FUNCTIONS
###############################################################################

log_info() { printf "${BLUE}[INFO]${NC} %s\n" "$*" >&2; }
log_pass() { printf "${GREEN}[PASS]${NC} %s\n" "$*" >&2; }
log_fail() { printf "${RED}[FAIL]${NC} %s\n" "$*" >&2; }
log_warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$*" >&2; }

mkdir -p "$LOG_DIR"

###############################################################################
# PHASE 1: STATIC CODE ANALYSIS (Before execution)
###############################################################################

audit_install_sh() {
  log_info "Running static analysis on install.sh..."
  local failures=0
  local findings_file="${LOG_DIR}/audit-findings.txt"
  
  : >"$findings_file"
  
  # Check 1: Unquoted variable expansions
  log_info "  [Audit] Checking unquoted variable expansions..."
  if grep -n '\$[A-Z_][A-Z0-9_]*[^"]' install.sh | grep -v '\\$' | sed -n '1,20p' >> "$findings_file"; then
    log_warn "    Found potential unquoted variables (may be false positives)"
  fi
  
  # Check 2: Command substitution without quotes
  log_info "  [Audit] Checking unsafe command substitution..."
  if grep -n '$(.*)[^"]' install.sh | sed -n '1,10p' >> "$findings_file"; then
    log_warn "    Found potential unsafe command substitution"
  fi
  
  # Check 3: Use of eval
  if grep -n 'eval' install.sh >> "$findings_file"; then
    log_fail "    Found use of eval() - CRITICAL SECURITY RISK"
    ((failures++))
  fi
  
  # Check 4: Temporary file handling
  log_info "  [Audit] Checking temporary file handling..."
  if grep -E '/tmp/[^(]*\$\$' install.sh | sed -n '1,5p' >> "$findings_file"; then
    log_warn "    Found non-mktemp temp files (PID-based)"
  fi
  
  # Check 5: grep/sed without quotes
  if grep -nE "grep.*\$|sed.*\$" install.sh | sed -n '1,10p' >> "$findings_file"; then
    log_warn "    Found grep/sed with unquoted variables"
  fi
  
  # Check 6: Dangerous database password assumptions
  if grep -n "postgresql://" install.sh >> "$findings_file"; then
    log_warn "    Found hardcoded PostgreSQL URL pattern - verify special char handling"
  fi
  
  # Check 7: File operations without validation
  if grep -nE 'read.*<.*\$|cat.*\$' install.sh | sed -n '1,5p' >> "$findings_file"; then
    log_warn "    Found unvalidated file reads"
  fi
  
  log_info "  Static analysis complete. See: $findings_file"
  return $((failures > 0 ? 1 : 0))
}

###############################################################################
# PHASE 2: INPUT VALIDATION TESTS
###############################################################################

test_input_validation() {
  log_info "Testing input validation..."
  local test_file="${LOG_DIR}/validation-tests.txt"
  local failures=0

  expect_failure() {
    local expected="$1" output
    shift
    if output="$("$@" 2>&1)"; then
      return 1
    fi
    grep -qF "$expected" <<<"$output"
  }
  : >"$test_file"
  
  # Test 1: Port validation
  log_info "  [Test] Port number validation..."
  if expect_failure "out of range" env HP_PANEL_PORT=99999 bash ./install.sh --check; then
    log_pass "    Port validation working"
  else
    log_fail "    Port validation FAILED - allows invalid port"
    echo "FAIL: Port validation" >> "$test_file"
    failures=$((failures + 1))
  fi
  
  # Test 2: Invalid MTA
  if expect_failure "Unsupported MTA" env HP_MAIL_MTA=sendmail bash ./install.sh --check; then
    log_pass "    MTA validation working"
  else
    log_fail "    MTA validation FAILED"
    echo "FAIL: MTA validation" >> "$test_file"
    failures=$((failures + 1))
  fi
  
  # Test 3: Invalid role
  if expect_failure "Unsupported role" bash ./install.sh --check --role invalid; then
    log_pass "    Role validation working"
  else
    log_fail "    Role validation FAILED"
    echo "FAIL: Role validation" >> "$test_file"
    failures=$((failures + 1))
  fi
  
  return $((failures > 0 ? 1 : 0))
}

test_fqdn_validation() {
  local os_name="$1" container_id="$2" output
  log_info "[$os_name] Testing invalid FQDN rejection..."
  if output=$(docker exec -e HP_PANEL_HOST=localhost "$container_id" \
      bash /root/install.sh --check 2>&1); then
    log_fail "[$os_name] Invalid FQDN was accepted"
    echo "$output" >> "${LOG_DIR}/${os_name}-fqdn.log"
    return 1
  fi
  output=$(tail -20 <<<"$output")
  if grep -q "valid FQDN" <<<"$output"; then
    log_pass "[$os_name] Invalid FQDN rejected"
    return 0
  fi
  log_fail "[$os_name] Invalid FQDN failed without the expected diagnostic"
  echo "$output" >> "${LOG_DIR}/${os_name}-fqdn.log"
  return 1
}

###############################################################################
# PHASE 3: DRY-RUN TESTS (Check-only mode, no changes)
###############################################################################

test_preflight_check() {
  local os_name="$1"
  local container_id="$2"
  
  log_info "[$os_name] Running preflight check (--check mode)..."
  
  # Copy install.sh into container
  docker cp install.sh "$container_id:/root/" >/dev/null 2>&1 || {
    log_fail "[$os_name] Could not copy install.sh to container"
    return 1
  }
  
  # Run preflight check
  local output
  output=$(docker exec \
    -e HP_PANEL_HOST=panel.example.test \
    -e HP_PANEL_ADMIN_CIDR=192.0.2.0/24 \
    "$container_id" bash /root/install.sh --check 2>&1 | tail -20)
  
  if echo "$output" | grep -q "Preflight passed"; then
    log_pass "[$os_name] Preflight check passed"
    return 0
  else
    log_fail "[$os_name] Preflight check failed"
    echo "$output" | sed -n '1,10p' >> "${LOG_DIR}/${os_name}-preflight.log"
    return 1
  fi
}

###############################################################################
# PHASE 4: MINIMAL INSTALLATION TEST (No roles, just check dependencies)
###############################################################################

test_minimal_install() {
  local os_name="$1"
  local container_id="$2"
  
  log_info "[$os_name] Running minimal dry-run install..."
  
  local output
  output=$(docker exec \
    -e HP_PANEL_HOST=panel.example.test \
    -e HP_PANEL_ADMIN_CIDR=192.0.2.0/24 \
    "$container_id" bash /root/install.sh --dry-run --role web 2>&1 | tail -30)
  
  if echo "$output" | grep -q "No changes were made"; then
    log_pass "[$os_name] Dry-run completed without errors"
    return 0
  else
    log_fail "[$os_name] Dry-run failed or didn't complete"
    echo "$output" >> "${LOG_DIR}/${os_name}-dryrun.log"
    return 1
  fi
}

###############################################################################
# PHASE 5: DEPENDENCY VERIFICATION
###############################################################################

test_package_detection() {
  local os_name="$1"
  local container_id="$2"
  
  log_info "[$os_name] Testing package manager detection..."
  
  # Check if apt or dnf detected correctly
  local has_apt
  has_apt=$(docker exec "$container_id" sh -lc 'command -v apt-get >/dev/null 2>&1' && echo 1 || echo 0)
  local has_dnf
  has_dnf=$(docker exec "$container_id" sh -lc 'command -v dnf >/dev/null 2>&1' && echo 1 || echo 0)
  
  local pkg_family="${PKG_MANAGERS[$os_name]}"
  
  if [[ "$pkg_family" == "debian" && $has_apt -eq 1 ]] || [[ "$pkg_family" == "rhel" && $has_dnf -eq 1 ]]; then
    log_pass "[$os_name] Package manager detected correctly"
    return 0
  else
    log_fail "[$os_name] Package manager detection mismatch"
    return 1
  fi
}

check_preflight_command() {
  local container_id="$1" command="$2"
  docker exec "$container_id" sh -lc "command -v '$command' >/dev/null 2>&1"
}

verify_preflight_prerequisites() {
  local os_name="$1" container_id="$2" command
  local missing=()
  for command in "${PREFLIGHT_COMMANDS[@]}"; do
    if ! check_preflight_command "$container_id" "$command"; then
      missing+=("$command")
    fi
  done
  if ((${#missing[@]})); then
    log_fail "[$os_name] Missing matrix prerequisites after provisioning: ${missing[*]}"
    return 1
  fi
  log_pass "[$os_name] Preflight runtime prerequisites verified"
}

record_preflight_prerequisites() {
  local os_name="$1" container_id="$2" image="$3"
  local family="${PKG_MANAGERS[$os_name]}"
  local packages="${PREFLIGHT_PACKAGES[$family]}"
  local report="${LOG_DIR}/${os_name}-prerequisites.txt" image_identity
  image_identity="$(docker image inspect "$image" --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}')"     || { log_fail "[$os_name] Could not inspect matrix image identity"; return 1; }
  {
    printf 'image_reference=%s
' "$image"
    printf 'image_identity=%s
' "$image_identity"
    printf 'package_family=%s
' "$family"
    if [[ "$family" == debian ]]; then
      docker exec "$container_id" sh -lc "dpkg-query -W $packages"
    else
      docker exec "$container_id" sh -lc "rpm -q --qf '%{NAME}=%{VERSION}-%{RELEASE}\n' $packages"
    fi
  } >"$report" || {
    log_fail "[$os_name] Could not record prerequisite metadata"
    return 1
  }
  log_pass "[$os_name] Prerequisite metadata recorded in $report"
}

provision_preflight_prerequisites() {
  local os_name="$1" container_id="$2" image="${OS_IMAGES[$1]}"
  local family="${PKG_MANAGERS[$os_name]}"
  local packages="${PREFLIGHT_PACKAGES[$family]}"
  local install_log="${LOG_DIR}/${os_name}-prerequisite-install.log"
  log_info "[$os_name] Provisioning preflight runtime prerequisites..."
  if [[ "$family" == debian ]]; then
    docker exec -e DEBIAN_FRONTEND=noninteractive "$container_id" sh -lc       "apt-get update && apt-get install -y --no-install-recommends $packages"       >"$install_log" 2>&1 || {
        log_fail "[$os_name] Matrix prerequisite provisioning failed; see $install_log"
        return 1
      }
  else
    docker exec "$container_id" sh -lc       "dnf -y -q install $packages" >"$install_log" 2>&1 || {
        log_fail "[$os_name] Matrix prerequisite provisioning failed; see $install_log"
        return 1
      }
  fi
  verify_preflight_prerequisites "$os_name" "$container_id" || return 1
  record_preflight_prerequisites "$os_name" "$container_id" "$image"
}

###############################################################################
# PHASE 6: CRITICAL FILE CHECKS
###############################################################################

verify_install_sh_integrity() {
  log_info "Verifying install.sh integrity..."
  
  # Check file exists
  if [[ ! -f "install.sh" ]]; then
    log_fail "install.sh not found!"
    return 1
  fi
  
  # Check shebang
  if ! head -1 install.sh | grep -q "^#!/usr/bin/env bash"; then
    log_fail "Invalid shebang"
    return 1
  fi
  
  # Check critical functions exist
  local required_functions=("random_alnum" "config_value" "pkg_install" "installer_exit")
  for func in "${required_functions[@]}"; do
    if grep -q "^$func()" install.sh; then
      log_pass "  Function '$func' found"
    else
      log_fail "  Function '$func' MISSING"
      return 1
    fi
  done
  
  # Check trap is set
  if grep -q "trap installer_exit EXIT" install.sh; then
    log_pass "  EXIT trap is configured"
  else
    log_fail "  EXIT trap MISSING - error handling incomplete"
    return 1
  fi
  
  return 0
}

###############################################################################
# PHASE 7: DOCKER CONTAINER TESTS
###############################################################################

run_container_tests() {
  local os_name="$1"
  local image="${OS_IMAGES[$os_name]}"
  
  log_info "[$os_name] Starting container test..."
  
  # Create container with minimal setup
  local container_id
  container_id=$(docker run -d \
    --rm \
    --tmpfs /tmp:exec \
    --tmpfs /run:exec \
    --cpus 2 \
    --memory 2g \
    "$image" \
    sleep 3600) || {
    log_fail "[$os_name] Could not create container"
    return 1
  }
  
  log_info "[$os_name] Container ID: $container_id"
  
  # Detect the native package manager before changing the minimal image.
  local test_results=()
  test_package_detection "$os_name" "$container_id" && test_results+=(1) || test_results+=(0)
  if ! provision_preflight_prerequisites "$os_name" "$container_id"; then
    docker stop "$container_id" >/dev/null 2>&1 || true
    return 1
  fi

  test_preflight_check "$os_name" "$container_id" && test_results+=(1) || test_results+=(0)
  test_fqdn_validation "$os_name" "$container_id" && test_results+=(1) || test_results+=(0)
  test_minimal_install "$os_name" "$container_id" && test_results+=(1) || test_results+=(0)
  
  # Cleanup
  docker stop "$container_id" >/dev/null 2>&1 || true
  
  # Report results
  local passed=0
  for result in "${test_results[@]}"; do
    ((passed += result))
  done
  
  log_info "[$os_name] Results: $passed/${#test_results[@]} tests passed"
  
  return $((passed == ${#test_results[@]} ? 0 : 1))
}

###############################################################################
# PHASE 8: GENERATE REPORT
###############################################################################

generate_report() {
  log_info "Generating test report..."
  
  cat > "$REPORT_FILE" <<'EOF'
# HostPanel Installer Test Report

## Summary

This report details the comprehensive testing of `install.sh` across all supported operating systems.

### Configured Test Phases

1. Static code analysis
2. Input validation tests
3. Preflight checks (`--check` mode)
4. Dry-run installation (`--dry-run` mode)
5. Package-manager detection
6. Container integration tests
7. Critical-file integrity

## Operating Systems Tested

| OS | Status | Notes |
|----|--------|-------|
| Ubuntu 22.04 | configured | Debian-family, LTS |
| Ubuntu 24.04 | configured | Debian-family, LTS |
| Ubuntu 26.04 | configured | Debian-family, LTS; native PHP/Rspamd |
| Debian 12 | configured | bookworm |
| Debian 13 | configured | trixie |
| Rocky Linux 9 | configured | RHEL-family |
| Rocky Linux 10 | configured | RHEL-family |
| AlmaLinux 9 | configured | RHEL-family |
| AlmaLinux 10 | configured | RHEL-family |

## Critical Issues Found

See `audit-findings.txt` for detailed findings.

## Recommendations

1. Fix all CRITICAL-level bugs before production
2. Implement continuous integration testing
3. Add ShellCheck linting to CI pipeline
4. Review all temporary file operations
5. Validate all user-supplied environment variables

EOF
  
  log_pass "Report generated: $REPORT_FILE"
}

###############################################################################
# MAIN
###############################################################################

main() {
  log_info "HostPanel Installer Comprehensive Test Suite"
  log_info "============================================"
  
  # Phase 1: Static analysis
  log_info ""
  log_info "PHASE 1: Static Analysis"
  if ! audit_install_sh; then
    log_warn "Some static analysis issues found (see audit-findings.txt)"
  fi
  
  # Phase 2: Integrity check
  log_info ""
  log_info "PHASE 2: File Integrity"
  if ! verify_install_sh_integrity; then
    log_fail "Critical integrity check failed"
    exit 1
  fi
  
  # Phase 3: Input validation
  log_info ""
  log_info "PHASE 3: Input Validation"
  if ! test_input_validation; then
    log_fail "Input validation regression checks failed"
    exit 1
  fi
  
  # Phase 4: Container tests (if Docker available and not CHECK_ONLY)
  if [[ $CHECK_ONLY -eq 0 ]] && command -v docker >/dev/null 2>&1; then
    log_info ""
    log_info "PHASE 4: Container Integration Tests"
    
    local os_list=(ubuntu-22.04 ubuntu-24.04 ubuntu-26.04 debian-12 debian-13 rocky-9 rocky-10 almalinux-9 almalinux-10)
    
    if [[ -n "$TARGET_OS" ]]; then
      os_list=("$TARGET_OS")
    fi
    
    for os_name in "${os_list[@]}"; do
      if [[ -v OS_IMAGES[$os_name] ]]; then
        run_container_tests "$os_name"
      fi
    done
  else
    log_info ""
    log_info "PHASE 4: Container tests skipped (CHECK_ONLY or Docker unavailable)"
  fi
  
  # Generate report
  log_info ""
  log_info "PHASE 5: Report Generation"
  generate_report
  
  log_info ""
  log_pass "Test suite complete. Results in: $LOG_DIR"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only) CHECK_ONLY=1; shift ;;
    --verbose) VERBOSE=1; shift ;;
    --os)
      [[ $# -ge 2 ]] || { echo "--os requires a value" >&2; exit 1; }
      TARGET_OS="$2"
      [[ -v OS_IMAGES[$TARGET_OS] ]] || { echo "Unknown OS target: $TARGET_OS" >&2; exit 1; }
      shift 2
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
