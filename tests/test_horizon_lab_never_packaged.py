# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The private laboratory must never reach a distributable artifact.

`lab/` holds the scientific record: runners, development results, dataset manifests and — under
`lab/datasets/raw/` — an external corpus with its own licence plus the sealed one-shot holdout.
None of it may be packaged or redistributed. Packaging is governed by `packages.find where=["src"]`,
which is easy to widen by accident; these tests make that accident loud.
"""
from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_setuptools_only_discovers_the_src_tree() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]


def test_no_shipped_module_imports_the_laboratory() -> None:
    # Anchored to real import statements: prose such as "learned from labels" must not trip this.
    importer = re.compile(r"^\s*(?:from\s+lab(?:\.|\s)|import\s+lab(?:\.|\s|$))", re.MULTILINE)
    offenders = [
        path.relative_to(ROOT)
        for path in (ROOT / "src").rglob("*.py")
        if importer.search(path.read_text())
    ]
    assert not offenders, f"shipped modules import the laboratory: {offenders}"


def test_raw_corpus_is_git_ignored() -> None:
    """The external corpus and the sealed holdout must never be versioned."""
    raw = ROOT / "lab/datasets/raw"
    if not raw.exists():
        return
    for path in raw.rglob("*"):
        if not path.is_file():
            continue
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(path.relative_to(ROOT))],
            cwd=ROOT, capture_output=True,
        )
        assert ignored.returncode == 0, f"raw corpus file is not git-ignored: {path}"


def test_sealed_holdout_is_not_referenced_by_shipped_code() -> None:
    offenders = [
        path.relative_to(ROOT)
        for path in (ROOT / "src").rglob("*.py")
        if "drop_dataset_dev" in path.read_text()
    ]
    assert not offenders, f"shipped code references the sealed holdout: {offenders}"
