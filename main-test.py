#!/usr/bin/env python3
"""
Main test runner for the real-time translation system.

This script runs all tests with proper configuration to avoid hanging issues
and provides comprehensive test results.
"""

import subprocess
import sys
import os
import multiprocessing
from datetime import datetime

def run_tests():
    """Run all tests for the real-time translation system."""
    
    # Set multiprocessing start method to avoid hanging tests
    multiprocessing.set_start_method("spawn", force=True)
    
    # Define test files to run
    test_files = [
        "tests/test_ai_models.py",
        "tests/test_audio.py", 
        "tests/test_direct_adapter.py",
        "tests/test_ipc_communication.py",
        "tests/test_tts_engine.py",
        "tests/test_integration.py",
        "tests/test_service_status.py",
        "tests/test_ui_components.py"
    ]
    
    # Filter out test files that don't exist
    existing_tests = []
    for test_file in test_files:
        if os.path.exists(test_file):
            existing_tests.append(test_file)
        else:
            print(f"Warning: Test file {test_file} does not exist, skipping...")
    
    if not existing_tests:
        print("No test files found!")
        return 1
    
    print(f"Running tests: {', '.join(existing_tests)}")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    # Prepare the pytest command
    cmd = [
        sys.executable, "-m", "pytest"
    ] + existing_tests + [
        "-v",  # verbose
        "--tb=short",  # traceback format
        "-x",  # stop on first failure (optional, remove if you want all results)
    ]
    
    # Add timeout for each test to prevent hanging
    try:
        # Run the tests with subprocess
        result = subprocess.run(
            cmd,
            cwd=os.getcwd(),
            timeout=300,  # 5 minute timeout for the entire test run
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        
        print("="*80)
        print(f"Test run completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Return code: {result.returncode}")
        
        return result.returncode
        
    except subprocess.TimeoutExpired:
        print("ERROR: Test run timed out after 5 minutes")
        return 1
    except Exception as e:
        print(f"ERROR running tests: {e}")
        return 1

def run_tests_with_output_file(output_file="test-results.md"):
    """Run tests and save output to a file."""
    
    # Set multiprocessing start method to avoid hanging tests
    multiprocessing.set_start_method("spawn", force=True)
    
    # Define test files to run
    all_test_files = [
        "tests/test_ai_models.py",
        "tests/test_audio.py",
        "tests/test_direct_adapter.py",
        "tests/test_ipc_communication.py",
        "tests/test_tts_engine.py",
        "tests/test_integration.py",
        "tests/test_service_status.py",
        "tests/test_ui_components.py"
    ]
    
    # Filter out test files that don't exist or have missing dependencies
    existing_tests = []
    for test_file in all_test_files:
        if os.path.exists(test_file):
            # Check if it's a UI-related test that might have PySide6 dependency
            if any(ui_test in test_file for ui_test in ['test_integration.py', 'test_service_status.py', 'test_ui_components.py']):
                # Try to import PySide6 to see if it's available
                try:
                    import importlib.util
                    spec = importlib.util.find_spec("PySide6")
                    if spec is not None:
                        existing_tests.append(test_file)
                    else:
                        print(f"Warning: Skipping {test_file} - PySide6 not available")
                except ImportError:
                    print(f"Warning: Skipping {test_file} - PySide6 not available")
            else:
                # Non-UI tests are always included
                existing_tests.append(test_file)
        else:
            print(f"Warning: Test file {test_file} does not exist, skipping...")
    
    if not existing_tests:
        print("No test files found!")
        return 1
    
    print(f"Running tests: {', '.join(existing_tests)}")
    print(f"Output will be saved to: {output_file}")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    # Prepare the pytest command
    cmd = [
        sys.executable, "-m", "pytest"
    ] + existing_tests + [
        "-v",  # verbose
        "--tb=short",  # traceback format
    ]
    
    # Run tests and redirect output to file
    try:
        with open(output_file, 'w') as f:
            # Write header
            f.write(f"Test Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*80 + "\n")
            
            # Run the tests with subprocess and redirect to file
            result = subprocess.run(
                cmd,
                cwd=os.getcwd(),
                timeout=600,  # 10 minute timeout for the entire test run
                stdout=f,
                stderr=subprocess.STDOUT
            )
            
            # Append footer
            f.write("\n" + "="*80 + "\n")
            f.write(f"Test run completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Return code: {result.returncode}\n")
        
        print(f"Test results saved to: {output_file}")
        print(f"Return code: {result.returncode}")
        
        return result.returncode
        
    except subprocess.TimeoutExpired:
        print("ERROR: Test run timed out after 10 minutes")
        # Still write timeout info to file
        with open(output_file, 'a') as f:
            f.write(f"\nERROR: Test run timed out after 10 minutes\n")
            f.write(f"Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        return 1
    except Exception as e:
        print(f"ERROR running tests: {e}")
        # Still write error info to file
        with open(output_file, 'a') as f:
            f.write(f"\nERROR running tests: {e}\n")
            f.write(f"Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        return 1

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--output":
        # Run with output file
        output_file = sys.argv[2] if len(sys.argv) > 2 else "test-results.md"
        exit_code = run_tests_with_output_file(output_file)
    else:
        # Run normally
        exit_code = run_tests()
    
    sys.exit(exit_code)