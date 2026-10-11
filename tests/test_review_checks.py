# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Runs the check groups of review_checks.py under pytest.

review_checks.py collects named checks per group (it can also be run on its
own: python tests/review_checks.py). Each group becomes one pytest test that
fails with the names of the checks that did not pass.
"""
import pytest

import review_checks as rc

GROUPS = [
    "test_config",
    "test_interface",
    "test_roundtrip",
    "test_word_table_headers",
    "test_chunking_and_reassembly",
    "test_quality_features",
    "test_glossary_and_budget",
    "test_engine_resilience",
]


@pytest.mark.parametrize("group", GROUPS)
def test_group(group):
    rc.RESULTS.clear()
    getattr(rc, group)()
    failed = [name for name, passed in rc.RESULTS if not passed]
    assert rc.RESULTS, f"{group} recorded no checks"
    assert not failed, f"failed checks: {failed}"
