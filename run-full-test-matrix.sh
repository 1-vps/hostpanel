#!/usr/bin/env bash
#
# HostPanel Installation Test Suite - Multi-OS Matrix Testing
# Tests install.sh across all 8 supported operating systems
#
# Usage:
#   ./run-full-test-matrix.sh [--quick] [--os OS_NAME] [--verbose]
#
# Supported OSes: ubuntu-22.04, ubuntu-24.04, debian-12, debian-13,
#                 rocky-9, rocky-10, almalinux-9, almalinux-10

set -euo pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_LOG_DIR="${SCRIPT_DIR}/test-logs-$(date +%Y%m%d-%H%M%S)"
REPORT_FILE="${TEST_LOG_DIR}/test-report.md"
RESULTS_FILE="${TEST_LOG_DIR}/test-results.json"

# Test matrix
declare -A OS_IMAGES=(
  [ubuntu-22.04]="ubuntu:22.04"
  [ubuntu-24.04]="ubuntu:24.04"
  [debian-12]="debian:12"
  [debian-13]="debian:13"
  [rocky-9]="rockylinux:9"
  [rocky-10]="rockylinux:10"
  [almalinux-9]="almalinux:9"
  [almalinux-10]="almalinux:10"
)

declare -A PKG_MANAGERS=(
  [ubuntu-22.04]="debian"
  [ubuntu-24.04]="debian"
  [debian-12]="debian"
  [debian-13]="debian"
  [rocky-9]="rhel"
  [rocky-10]="rhel"
  [almalinux-9]="rhel"
  [almalinux-10]="rhel"
)

# Test parameters
QUICK_MODE=0
VERBOSE=0
TARGET_OS=""
TIMEOUT_PREFLIGHT=60
TIMEOUT_DRYRUN=120
CONTAINER_CPU=2
CONTAINER_MEM="2g"

# Results tracking
declare -a TEST_RESULTS=()
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

log_info()    { printf "\033[0;34m[INFO]\033[0m    %s\n" "$*" >&2; }
log_pass()    { printf "\033[0;32m[PASS]\033[0m    %s\n" "$*" >&2; }
log_fail()    { printf "\033[0;31m[FAIL]\033[0m    %s\n" "$*" >&2; }
log_warn()    { printf "\033[0;33m[WARN]\033[0m    %s\n" "$*" >&2; }
log_debug()   { [[ $VERBOSE -eq 1 ]] && printf "\033[0;36m[DEBUG]\033[0m   %s\n" "$*" >&2; }

record_test() {
  local os_name="$1"
  local test_name="$2"
  local result="$3"  # PASS or FAIL
  local details="${4:-}"
  
  ((TOTAL_TESTS++))
  
  if [[ "$result" == "PASS" ]]; then
    ((PASSED_TESTS++))
    log_pass "[$os_name] $test_name"
  else
    ((FAILED_TESTS++))
    log_fail "[$os_name] $test_name"
    [[ -n "$details" ]] && echo "  Details: $details" >> "${TEST_LOG_DIR}/${os_name}-failures.log"
  fi
  
  TEST_RESULTS+=("$os_name|$test_name|$result|$details")
}

# ============================================================================
# PHASE 1: PRE-FLIGHT CHECKS
# ============================================================================

check_prerequisites() {
  log_info "Checking prerequisites..."
  
  # Check Docker
  if ! command -v docker >/dev/null 2>&1; then
    log_fail "Docker is not installed or not in PATH"
    return 1
  fi
  
  # Check Docker daemon
  if ! docker ps >/dev/null 2>&1; then
    log_fail "Docker daemon is not running or user has no access"
    return 1
  fi
  
  # Check for install.sh
  if [[ ! -f "$SCRIPT_DIR/install.sh" ]]; then
    log_fail "install.sh not found in $SCRIPT_DIR"
    return 1
  fi
  
  # Check for test data
  if [[ ! -f "$SCRIPT_DIR/INSTALL_SH_BUG_AUDIT.md" ]]; then
    log_warn "Bug audit report not found - tests will be limited"
  fi
  
  log_pass "All prerequisites met"
  return 0
}

# ============================================================================
# PHASE 2: STATIC CODE ANALYSIS
# ============================================================================

run_shellcheck() {
  log_info "Running ShellCheck on install.sh..."
  
  if ! command -v shellcheck >/dev/null 2>&1; then
    log_warn "ShellCheck not installed - skipping static analysis"
    return 0
  fi
  
  local shellcheck_log="${TEST_LOG_DIR}/shellcheck-report.txt"
  
  if shellcheck -S style -x install.sh > "$shellcheck_log" 2>&1; then
    log_pass "ShellCheck passed with no issues"
    return 0
  else
    local issue_count
    issue_count=$(grep -c "^" "$shellcheck_log" || echo 0)
    log_warn "ShellCheck found $issue_count issues (see $shellcheck_log)"
    head -20 "$shellcheck_log" >> "${TEST_LOG_DIR}/analysis-issues.log"
    return 1
  fi
}

audit_security_patterns() {
  log_info "Running security pattern analysis..."
  
  local audit_log="${TEST_LOG_DIR}/security-audit.txt"
  : > "$audit_log"
  
  local found_issues=0
  
  # Check for eval usage
  if grep -n '\beval\b' install.sh >> "$audit_log" 2>&1; then
    log_fail "Found 'eval' usage - CRITICAL SECURITY RISK"
    ((found_issues++))
  fi
  
  # Check for unquoted variables in dangerous contexts
  if grep -nE '(\$\{?[A-Z_][A-Z0-9_]*\}?[^"]|\|\|)' install.sh | grep -v '\\$' | head -5 >> "$audit_log" 2>&1; then
    log_warn "Found potentially unquoted variables (verify manually)"
  fi
  
  # Check for hardcoded passwords/secrets
  if grep -nE '(password|secret|token|key)' install.sh | grep -iE '=['\''"]' >> "$audit_log" 2>&1; then
    log_warn "Found potential hardcoded credentials (verify)"
  fi
  
  # Check for SQL injection risks
  if grep -n "<<SQL" install.sh | head -3 >> "$audit_log" 2>&1; then
    log_warn "Found SQL heredocs - verify parameterization"
  fi
  
  if [[ $found_issues -eq 0 ]]; then
    log_pass "No critical security patterns detected"
    return 0
  else
    log_warn "Found $found_issues potential security issues"
    return 1
  fi
}

# ============================================================================
# PHASE 3: DOCKER CONTAINER TESTS
# ============================================================================

setup_container() {
  local os_name="$1"
  local image="${OS_IMAGES[$os_name]}"
  
  log_debug "Creating container for $os_name from image $image..."
  
  # Try to pull image
  if ! docker pull "$image" >/dev/null 2>&1; then
    log_fail "[$os_name] Could not pull Docker image: $image"
    return 1
  fi
  
  # Create container
  local container_id
  container_id=$(docker run -d \
    --rm \
    --tmpfs /tmp:exec \
    --tmpfs /run:exec \
    --cpus "$CONTAINER_CPU" \
    --memory "$CONTAINER_MEM" \
    --name "hostpanel-test-$$-${RANDOM}" \
    "$image" \
    sleep 3600) || {
    log_fail "[$os_name] Could not create container"
    return 1
  }
  
  echo "$container_id"
  return 0
}

test_preflight_check() {
  local os_name="$1"
  local container_id="$2"
  
  log_debug "[$os_name] Testing preflight check..."
  
  # Copy install.sh
  if ! docker cp install.sh "$container_id:/root/" >/dev/null 2>&1; then
    record_test "$os_name" "preflight_setup" "FAIL" "Could not copy install.sh"
    return 1
  fi
  
  # Update package lists
  docker exec "$container_id" \
    bash -c 'if command -v apt-get >/dev/null 2>&1; then 
      apt-get update >/dev/null 2>&1
    elif command -v dnf >/dev/null 2>&1; then 
      dnf makecache >/dev/null 2>&1
    fi' >/dev/null 2>&1 || true
  
  # Run preflight check with timeout
  local output
  output=$(timeout "$TIMEOUT_PREFLIGHT" docker exec "$container_id" \
    bash /root/install.sh --check 2>&1 | tail -50) || {
    record_test "$os_name" "preflight_check" "FAIL" "Timeout or error"
    echo "$output" >> "${TEST_LOG_DIR}/${os_name}-preflight.log"
    return 1
  }
  
  if echo "$output" | grep -q "Preflight passed"; then
    record_test "$os_name" "preflight_check" "PASS"
    return 0
  else
    record_test "$os_name" "preflight_check" "FAIL" "Output missing 'Preflight passed'"
    echo "$output" >> "${TEST_LOG_DIR}/${os_name}-preflight.log"
    return 1
  fi
}

test_dryrun() {
  local os_name="$1"
  local container_id="$2"
  
  log_debug "[$os_name] Testing dry-run installation..."
  
  # Run dry-run with timeout
  local output
  output=$(timeout "$TIMEOUT_DRYRUN" docker exec "$container_id" \
    bash /root/install.sh --dry-run --role web 2>&1 | tail -50) || {
    record_test "$os_name" "dryrun" "FAIL" "Timeout or execution error"
    echo "$output" >> "${TEST_LOG_DIR}/${os_name}-dryrun.log"
    return 1
  }
  
  if echo "$output" | grep -q "No changes were made\|would be created\|would install"; then
    record_test "$os_name" "dryrun" "PASS"
    return 0
  else
    record_test "$os_name" "dryrun" "FAIL" "Unexpected output"
    echo "$output" >> "${TEST_LOG_DIR}/${os_name}-dryrun.log"
    return 1
  fi
}

test_package_detection() {
  local os_name="$1"
  local container_id="$2"
  local expected_pm="${PKG_MANAGERS[$os_name]}"
  
  log_debug "[$os_name] Testing package manager detection..."
  
  local has_apt
  has_apt=$(docker exec "$container_id" command -v apt-get >/dev/null 2>&1 && echo 1 || echo 0)
  local has_dnf
  has_dnf=$(docker exec "$container_id" command -v dnf >/dev/null 2>&1 && echo 1 || echo 0)
  
  if [[ "$expected_pm" == "debian" && $has_apt -eq 1 ]] || [[ "$expected_pm" == "rhel" && $has_dnf -eq 1 ]]; then
    record_test "$os_name" "package_manager_detection" "PASS"
    return 0
  else
    record_test "$os_name" "package_manager_detection" "FAIL" "PM detection mismatch"
    return 1
  fi
}

test_os_detection() {
  local os_name="$1"
  local container_id="$2"
  
  log_debug "[$os_name] Testing OS detection..."
  
  local os_info
  os_info=$(docker exec "$container_id" bash -c '. /etc/os-release 2>/dev/null && echo "$ID $VERSION_ID"')
  
  if [[ -n "$os_info" ]]; then
    record_test "$os_name" "os_detection" "PASS" "Detected: $os_info"
    return 0
  else
    record_test "$os_name" "os_detection" "FAIL" "Could not detect OS"
    return 1
  fi
}

test_environment_validation() {
  local os_name="$1"
  local container_id="$2"
  
  log_debug "[$os_name] Testing environment variable validation..."
  
  # Test invalid port
  local output
  output=$(docker exec "$container_id" bash -c \
    'HP_PANEL_PORT=99999 bash /root/install.sh --check 2>&1' | tail -10)
  
  if echo "$output" | grep -q "out of range\|invalid"; then
    record_test "$os_name" "env_validation_port" "PASS"
  else
    record_test "$os_name" "env_validation_port" "FAIL" "Port validation not enforced"
  fi
  
  # Test invalid MTA
  output=$(docker exec "$container_id" bash -c \
    'HP_MAIL_MTA=sendmail bash /root/install.sh --check 2>&1' | tail -10)
  
  if echo "$output" | grep -q "Unsupported\|invalid"; then
    record_test "$os_name" "env_validation_mta" "PASS"
  else
    record_test "$os_name" "env_validation_mta" "FAIL" "MTA validation not enforced"
  fi
}

run_container_tests() {
  local os_name="$1"
  local image="${OS_IMAGES[$os_name]}"
  
  log_info "[$os_name] Starting container tests (image: $image)..."
  
  # Setup container
  local container_id
  container_id=$(setup_container "$os_name") || {
    record_test "$os_name" "container_setup" "FAIL"
    return 1
  }
  
  record_test "$os_name" "container_setup" "PASS"
  log_debug "[$os_name] Container ID: $container_id"
  
  # Run tests
  test_os_detection "$os_name" "$container_id"
  test_package_detection "$os_name" "$container_id"
  test_environment_validation "$os_name" "$container_id"
  test_preflight_check "$os_name" "$container_id"
  
  if [[ $QUICK_MODE -eq 0 ]]; then
    test_dryrun "$os_name" "$container_id"
  fi
  
  # Cleanup
  docker stop "$container_id" >/dev/null 2>&1 || true
  
  log_info "[$os_name] Container tests complete"
  return 0
}

# ============================================================================
# PHASE 4: REPORTING
# ============================================================================

generate_html_report() {
  local html_file="${TEST_LOG_DIR}/test-report.html"
  
  cat > "$html_file" <<'HTMLEOF'
<!DOCTYPE html>
<html>
<head>
  <title>HostPanel Installer Test Report</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
    .header { background: #333; color: white; padding: 20px; border-radius: 5px; }
    .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 20px 0; }
    .stat { background: white; padding: 15px; border-radius: 5px; text-align: center; }
    .stat-value { font-size: 24px; font-weight: bold; }
    .stat-label { color: #666; font-size: 12px; }
    .pass { color: #28a745; }
    .fail { color: #dc3545; }
    .warn { color: #ffc107; }
    table { width: 100%; border-collapse: collapse; background: white; margin: 20px 0; }
    th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
    th { background: #f8f9fa; font-weight: bold; }
    tr:hover { background: #f5f5f5; }
    .os-section { margin: 30px 0; }
    .os-header { background: #e9ecef; padding: 10px; border-left: 4px solid #007bff; margin-bottom: 10px; }
  </style>
</head>
<body>
  <div class="header">
    <h1>HostPanel Installer Test Report</h1>
    <p>Generated: $(date)</p>
  </div>
  
  <div class="summary">
    <div class="stat">
      <div class="stat-value">$(printf "%d" $TOTAL_TESTS)</div>
      <div class="stat-label">Total Tests</div>
    </div>
    <div class="stat">
      <div class="stat-value pass">$(printf "%d" $PASSED_TESTS)</div>
      <div class="stat-label">Passed</div>
    </div>
    <div class="stat">
      <div class="stat-value fail">$(printf "%d" $FAILED_TESTS)</div>
      <div class="stat-label">Failed</div>
    </div>
    <div class="stat">
      <div class="stat-value">$(printf "%.1f%%" $(awk "BEGIN {print 100*$PASSED_TESTS/$TOTAL_TESTS}"))</div>
      <div class="stat-label">Success Rate</div>
    </div>
  </div>
  
  <h2>Test Results by OS</h2>
  <table>
    <tr>
      <th>OS</th>
      <th>Test</th>
      <th>Result</th>
      <th>Details</th>
    </tr>
HTMLEOF

  for entry in "${TEST_RESULTS[@]}"; do
    IFS='|' read -r os test result details <<< "$entry"
    local result_class
    [[ "$result" == "PASS" ]] && result_class="pass" || result_class="fail"
    printf "    <tr>\n      <td>%s</td>\n      <td>%s</td>\n      <td class='%s'>%s</td>\n      <td>%s</td>\n    </tr>\n" \
      "$os" "$test" "$result_class" "$result" "$details" >> "$html_file"
  done
  
  cat >> "$html_file" <<'HTMLEOF'
  </table>
</body>
</html>
HTMLEOF

  log_pass "HTML report generated: $html_file"
}

generate_markdown_report() {
  local md_file="${TEST_LOG_DIR}/test-report.md"
  
  cat > "$md_file" <<EOF
# HostPanel Installer Test Report

**Generated**: $(date)

## Summary

| Metric | Value |
|--------|-------|
| Total Tests | $TOTAL_TESTS |
| Passed | $PASSED_TESTS |
| Failed | $FAILED_TESTS |
| Success Rate | $(printf "%.1f%%" $(awk "BEGIN {print 100*$PASSED_TESTS/$TOTAL_TESTS}")) |

## Test Results

### By Operating System

EOF

  # Group results by OS
  local current_os=""
  for entry in "${TEST_RESULTS[@]}"; do
    IFS='|' read -r os test result details <<< "$entry"
    if [[ "$os" != "$current_os" ]]; then
      current_os="$os"
      echo "#### $os" >> "$md_file"
      echo "" >> "$md_file"
    fi
    local symbol
    [[ "$result" == "PASS" ]] && symbol="✅" || symbol="❌"
    echo "- $symbol **$test**: $result" >> "$md_file"
    [[ -n "$details" ]] && echo "  - $details" >> "$md_file"
  done
  
  log_pass "Markdown report generated: $md_file"
}

# ============================================================================
# MAIN TEST EXECUTION
# ============================================================================

main() {
  mkdir -p "$TEST_LOG_DIR"
  
  log_info "HostPanel Installer Comprehensive Test Suite"
  log_info "=============================================="
  
  # Check prerequisites
  if ! check_prerequisites; then
    log_fail "Prerequisites not met"
    exit 1
  fi
  
  # Phase 1: Static analysis
  log_info ""
  log_info "PHASE 1: Static Code Analysis"
  run_shellcheck || true
  audit_security_patterns || true
  
  # Phase 2: Container tests
  log_info ""
  log_info "PHASE 2: Multi-OS Container Testing"
  
  local os_list=(ubuntu-22.04 ubuntu-24.04 debian-12 debian-13 rocky-9 almalinux-9)
  
  if [[ -n "$TARGET_OS" ]]; then
    [[ -v OS_IMAGES[$TARGET_OS] ]] || die "Unknown OS: $TARGET_OS"
    os_list=("$TARGET_OS")
  fi
  
  if [[ $QUICK_MODE -eq 1 ]]; then
    log_info "Quick mode: testing subset of OSes"
    os_list=(ubuntu-22.04 debian-12 rocky-9)
  fi
  
  for os_name in "${os_list[@]}"; do
    if ! run_container_tests "$os_name"; then
      log_warn "[$os_name] Some tests failed"
    fi
    log_info ""
  done
  
  # Phase 3: Generate reports
  log_info ""
  log_info "PHASE 3: Report Generation"
  generate_markdown_report
  generate_html_report
  
  # Summary
  log_info ""
  log_info "=============================================="
  log_pass "Test suite complete!"
  log_info "  Total: $TOTAL_TESTS | Passed: $PASSED_TESTS | Failed: $FAILED_TESTS"
  log_info "  Reports: $TEST_LOG_DIR"
  
  return $((FAILED_TESTS > 0 ? 1 : 0))
}

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick) QUICK_MODE=1; shift ;;
    --os) TARGET_OS="$2"; shift 2 ;;
    --verbose) VERBOSE=1; shift ;;
    --help)
      cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --quick       Run fewer tests (subset of OSes)
  --os OS_NAME  Test specific OS only
  --verbose     Enable debug output
  --help        Show this help

Supported OS names:
  ubuntu-22.04, ubuntu-24.04, debian-12, debian-13
  rocky-9, rocky-10, almalinux-9, almalinux-10

Examples:
  $0 --quick
  $0 --os ubuntu-22.04 --verbose
  $0
EOF
      exit 0 ;;
    *) log_fail "Unknown option: $1"; exit 1 ;;
  esac
done

main "$@"
