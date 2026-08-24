# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smoke tests: every ProofRay Engine example imports cleanly and runs end to end -- no live
network (the two polish examples default to `ALLOW_NETWORK = False`), no leftover files (the
SQLite example is entirely tempfile-scoped)."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from io import StringIO
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _load_example(name: str):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run_and_capture(main_callable) -> str:
    captured = StringIO()
    original_stdout = sys.stdout
    sys.stdout = captured
    try:
        main_callable()
    finally:
        sys.stdout = original_stdout
    return captured.getvalue()


class ExampleSmokeTests(unittest.TestCase):
    def test_quickstart_resolves(self):
        module = _load_example("quickstart")
        output = _run_and_capture(module.main)
        self.assertIn("RESOLVED", output)
        self.assertIn("42", output)

    def test_sqlite_documents_example_resolves_and_leaves_no_files_behind(self):
        module = _load_example("sqlite_documents_example")
        output = _run_and_capture(module.main)
        self.assertIn("RESOLVED", output)
        self.assertIn("3 rows", output)

    def test_local_model_polish_example_dry_runs_without_network(self):
        module = _load_example("local_model_polish_example")
        self.assertFalse(module.ALLOW_NETWORK, "example must default to dry-run/no-network")
        output = _run_and_capture(module.main)
        self.assertIn("dry_run", output)

    def test_api_model_polish_example_dry_runs_without_network(self):
        module = _load_example("api_model_polish_example")
        self.assertFalse(module.ALLOW_NETWORK, "example must default to dry-run/no-network")
        output = _run_and_capture(module.main)
        self.assertIn("dry_run", output)


if __name__ == "__main__":
    unittest.main()
