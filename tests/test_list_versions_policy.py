from unittest.mock import patch

import pytest

from src import cli_compat, utils
from src.versioning import parse_candidates


ISSUE_OUTPUT = "\n".join(
    [
        "INFO: Package name: com.google.android.apps.photos",
        "Most common compatible versions:",
        "\tAny",
    ]
)


def test_parse_candidates_preserves_unrestricted_policy_with_heading():
    candidates = parse_candidates(ISSUE_OUTPUT)

    assert candidates == []
    assert len(candidates) == 0
    assert bool(candidates) is True
    assert candidates.unrestricted is True


def test_supported_version_lookup_accepts_real_issue_output_as_unrestricted():
    with (
        patch.object(cli_compat, "detect_cli_kind", return_value=cli_compat.MORPHE),
        patch.object(utils, "run_process", return_value=ISSUE_OUTPUT),
    ):
        candidates = utils.get_supported_version_candidates(
            "com.google.android.apps.photos",
            "morphe-cli.jar",
            "patches.rvp",
        )

    assert candidates == []


def test_supported_version_lookup_still_rejects_unknown_policy():
    output = "\n".join(
        [
            "INFO: Package name: com.example.app",
            "Most common compatible versions:",
            "\tWhatever",
        ]
    )

    with (
        patch.object(cli_compat, "detect_cli_kind", return_value=cli_compat.MORPHE),
        patch.object(utils, "run_process", return_value=output),
        pytest.raises(utils.SupportedVersionLookupError),
    ):
        utils.get_supported_version_candidates(
            "com.example.app",
            "morphe-cli.jar",
            "patches.rvp",
        )
