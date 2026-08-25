import json
from pathlib import Path

import pytest

from proofray_app.embedded_equivalence import run_frozen_cases, verify_desktop_reference


def test_frozen_equivalence_manifest_has_exactly_twenty_unique_cases():
    path = Path(__file__).parents[1] / "proofray_app" / "frozen_equivalence_cases.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["schema"] == "proofray.app.embedded-equivalence-cases.v1"
    assert len(value["cases"]) == 20
    assert len({case["id"] for case in value["cases"]}) == 20
    assert len(value["desktop_result_sha256"]) == 64


def test_current_desktop_matches_the_complete_frozen_result():
    result = verify_desktop_reference()
    assert result["result_sha256"] == \
        "11ba3aa0ae2f96fe27d28dbaa664c1b0300d611c212d624766edddba0922a043"


@pytest.mark.slow
def test_frozen_equivalence_runner_is_deterministic():
    first = run_frozen_cases()
    second = run_frozen_cases()
    assert first == second
    assert len(first["cases"]) == 20
