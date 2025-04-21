#!/usr/bin/env python3
"""Test runner for the real-time translation system."""

import pytest
import sys
import os
import argparse
from typing import List
import logging

def setup_logging():
    """Configure logging for test runs."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('tests/test_run.log')
        ]
    )

def run_tests(args: List[str]) -> int:
    """Run pytest with specified arguments.
    
    Args:
        args: Command line arguments
        
    Returns:
        Exit code from pytest
    """
    # Default pytest arguments
    pytest_args = [
        '--verbose',
        '--color=yes'
    ]
    
    # Add coverage if requested
    if '--coverage' in args:
        args.remove('--coverage')
        pytest_args.extend([
            '--cov=src',
            '--cov-report=term-missing',
            '--cov-report=html:tests/coverage'
        ])
    
    # Add test selection arguments
    if '--unit-only' in args:
        args.remove('--unit-only')
        pytest_args.append('-m not integration and not gpu')
    elif '--integration-only' in args:
        args.remove('--integration-only')
        pytest_args.append('-m integration')
    elif '--gpu-only' in args:
        args.remove('--gpu-only')
        pytest_args.append('-m gpu')
    
    # Add remaining args
    pytest_args.extend([a for a in args if a != '--help'])
    
    return pytest.main(pytest_args)

def main():
    """Main entry point for test runner."""
    parser = argparse.ArgumentParser(
        description='Run tests for real-time translation system'
    )
    
    parser.add_argument(
        '--coverage',
        action='store_true',
        help='Generate coverage report'
    )
    
    parser.add_argument(
        '--unit-only',
        action='store_true',
        help='Run only unit tests'
    )
    
    parser.add_argument(
        '--integration-only',
        action='store_true',
        help='Run only integration tests'
    )
    
    parser.add_argument(
        '--gpu-only',
        action='store_true',
        help='Run only GPU tests'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose output'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    logger = logging.getLogger(__name__)
    
    try:
        # Run tests
        logger.info("Starting test run...")
        exit_code = run_tests(sys.argv[1:])
        logger.info(f"Test run completed with exit code: {exit_code}")
        sys.exit(exit_code)
        
    except Exception as e:
        logger.error(f"Error running tests: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()