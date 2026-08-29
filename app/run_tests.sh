#!/bin/bash
# Test Runner Script
# Usage: ./run_tests.sh [option]

set -e  # Exit on error

echo "=========================================="
echo "  Proxmox Platform - Test Suite Runner"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${YELLOW}ℹ ${NC}$1"
}

print_success() {
    echo -e "${GREEN}✓ ${NC}$1"
}

print_error() {
    echo -e "${RED}✗ ${NC}$1"
}

# Parse command line arguments
TEST_TYPE=${1:-all}

case $TEST_TYPE in
    "all")
        print_info "Running ALL tests..."
        pytest tests/ -v
        ;;
    
    "auth")
        print_info "Running AUTHENTICATION tests only..."
        pytest tests/test_auth.py -v -m auth
        ;;
    
    "vm")
        print_info "Running VM CRUD tests only..."
        pytest tests/test_vm.py -v -m vm
        ;;
    
    "docker")
        print_info "Running DOCKER provider tests only..."
        pytest tests/test_vm.py -v -m docker
        ;;
    
    "vsphere")
        print_info "Running VSPHERE provider tests only..."
        pytest tests/test_vm.py -v -m vsphere
        ;;
    
    "unit")
        print_info "Running UNIT tests only..."
        pytest tests/ -v -m unit
        ;;
    
    "integration")
        print_info "Running INTEGRATION tests only..."
        pytest tests/ -v -m integration
        ;;
    
    "coverage")
        print_info "Running tests with COVERAGE report..."
        pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html
        print_success "Coverage report generated in htmlcov/index.html"
        ;;
    
    "quick")
        print_info "Running QUICK tests (no slow tests)..."
        pytest tests/ -v -m "not slow"
        ;;
    
    "failed")
        print_info "Re-running FAILED tests from last run..."
        pytest tests/ -v --lf
        ;;
    
    "clean")
        print_info "Cleaning test databases and cache..."
        rm -f test*.db
        rm -rf .pytest_cache
        rm -rf htmlcov
        rm -rf .coverage
        rm -rf __pycache__
        find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        print_success "Test artifacts cleaned!"
        ;;
    
    "watch")
        print_info "Running tests in WATCH mode (re-runs on file changes)..."
        pytest-watch tests/ -- -v
        ;;
    
    "help")
        echo "Usage: ./run_tests.sh [option]"
        echo ""
        echo "Options:"
        echo "  all          - Run all tests (default)"
        echo "  auth         - Run authentication tests only"
        echo "  vm           - Run VM CRUD tests only"
        echo "  docker       - Run Docker provider tests only"
        echo "  vsphere      - Run VSphere provider tests only"
        echo "  unit         - Run unit tests only"
        echo "  integration  - Run integration tests only"
        echo "  coverage     - Run tests with coverage report"
        echo "  quick        - Run quick tests (exclude slow tests)"
        echo "  failed       - Re-run only failed tests from last run"
        echo "  clean        - Clean test databases and cache"
        echo "  watch        - Run tests in watch mode (requires pytest-watch)"
        echo "  help         - Show this help message"
        echo ""
        ;;
    
    *)
        print_error "Unknown option: $TEST_TYPE"
        echo "Run './run_tests.sh help' for usage information"
        exit 1
        ;;
esac

# Exit status
if [ $? -eq 0 ]; then
    echo ""
    print_success "All tests passed!"
    exit 0
else
    echo ""
    print_error "Some tests failed!"
    exit 1
fi