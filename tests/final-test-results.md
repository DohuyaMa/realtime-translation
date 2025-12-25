============================= test session starts ==============================
platform linux -- Python 3.12.12, pytest-8.4.2, pluggy-1.6.0 -- /nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/bin/python
cachedir: .pytest_cache
hypothesis profile 'ci' -> database=None, deadline=None, print_blob=True, derandomize=True, suppress_health_check=(HealthCheck.too_slow,)
rootdir: /home/dmaslo/real-time-transletor
plugins: hypothesis-6.136.9, typeguard-4.4.4
collecting ... collected 49 items / 3 errors

==================================== ERRORS ====================================
__________________ ERROR collecting tests/test_integration.py __________________
ImportError while importing test module '/home/dmaslo/real-time-transletor/tests/test_integration.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_integration.py:10: in <module>
    from src.ui.main_window import MainWindow
E   ModuleNotFoundError: No module named 'src.ui.main_window'
________________ ERROR collecting tests/test_service_status.py _________________
ImportError while importing test module '/home/dmaslo/real-time-transletor/tests/test_service_status.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_service_status.py:9: in <module>
    from src.ui.main_window import MainWindow
E   ModuleNotFoundError: No module named 'src.ui.main_window'
_________________ ERROR collecting tests/test_ui_components.py _________________
ImportError while importing test module '/home/dmaslo/real-time-transletor/tests/test_ui_components.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/nix/store/n34ywginm00ql3l97zxcr7zk3kq6z9v3-python3-3.12.12/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_ui_components.py:11: in <module>
    from src.ui.main_window import MainWindow
E   ModuleNotFoundError: No module named 'src.ui.main_window'
=============================== warnings summary ===============================
../../../nix/store/qr9w9xklgb5ddaqwc4fbqm0lkm8nzk2f-python3.12-transformers-4.57.1/lib/python3.12/site-packages/transformers/utils/hub.py:110
  /nix/store/qr9w9xklgb5ddaqwc4fbqm0lkm8nzk2f-python3.12-transformers-4.57.1/lib/python3.12/site-packages/transformers/utils/hub.py:110: FutureWarning: Using `TRANSFORMERS_CACHE` is deprecated and will be removed in v5 of Transformers. Use `HF_HOME` instead.
    warnings.warn(

../../../nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/_util.py:23
  /nix/store/mcshwhiak3cmj60ajpzx5i4889hjjlrw-python3.12-spacy-3.8.7/lib/python3.12/site-packages/spacy/cli/_util.py:23: DeprecationWarning: Importing 'parser.split_arg_string' is deprecated, it will only be available in 'shell_completion' in Click 9.0.
    from click.parser import split_arg_string

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
ERROR tests/test_integration.py
ERROR tests/test_service_status.py
ERROR tests/test_ui_components.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!!
======================== 2 warnings, 3 errors in 5.01s =========================
Test Results - 2025-12-23 16:22:37
================================================================================

================================================================================
Test run completed at: 2025-12-23 16:22:44
Return code: 2
