"""[DATA-005] data_source_replay - thin wrapper so the test path referenced in
TASKS.md acceptance criteria resolves correctly.

The actual tests live in test_data005_fixture_replay.py.
"""
import importlib
import sys
from pathlib import Path

_sys_path_appended = False
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))
    _sys_path_appended = True

_mod = importlib.import_module("test_data005_fixture_replay")

if _sys_path_appended:
    sys.path.pop(0)

globals().update(
    {k: v for k, v in vars(_mod).items() if k.startswith("Test") or k.startswith("test_")}
)
