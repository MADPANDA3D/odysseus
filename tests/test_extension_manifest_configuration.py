"""MAD-939: manifest configuration and API-key declarations."""

import json
from pathlib import Path

import pytest

from src.extension_registry import ExtensionContractError, validate_extension_manifest


FIXTURES = Path(__file__).parent / "fixtures" / "extensions"


def _manifest() -> dict:
    return json.loads((FIXTURES / "oracle.manifest.json").read_text(encoding="utf-8"))


def test_configuration_declarations_are_accepted_and_defaulted():
    manifest = _manifest()
    manifest["configuration"] = [
        {"key": "ORACLE_API_TOKEN", "description": "Owner-supplied token", "required": True, "secret": True},
        {"key": "ORACLE_BASE_URL", "description": "Optional base URL"},
    ]

    normalized = validate_extension_manifest(manifest)

    assert normalized["configuration"] == [
        {"key": "ORACLE_API_TOKEN", "description": "Owner-supplied token", "required": True, "secret": True},
        {"key": "ORACLE_BASE_URL", "description": "Optional base URL", "required": False, "secret": False},
    ]


def test_manifest_without_configuration_stays_valid_and_omits_the_field():
    normalized = validate_extension_manifest(_manifest())

    assert "configuration" not in normalized


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda manifest: manifest["configuration"][0].update({"key": "bad-key"}), "extension_configuration_key_invalid"),
        (
            lambda manifest: manifest["configuration"].append(
                {"key": "ORACLE_API_TOKEN", "description": "duplicate"}
            ),
            "extension_configuration_key_invalid",
        ),
        (
            lambda manifest: manifest["configuration"][0].update({"value": "secret-value"}),
            "extension_configuration_unknown_field",
        ),
        (
            lambda manifest: manifest["configuration"][0].update({"required": "yes"}),
            "extension_configuration_flag_invalid",
        ),
        (
            lambda manifest: manifest["configuration"][0].pop("description"),
            "extension_configuration_invalid",
        ),
    ],
)
def test_bad_configuration_fails_closed(mutate, code):
    manifest = _manifest()
    manifest["configuration"] = [{"key": "ORACLE_API_TOKEN", "description": "Owner-supplied token"}]
    mutate(manifest)

    with pytest.raises(ExtensionContractError, match=code):
        validate_extension_manifest(manifest)


def test_configuration_is_bounded():
    manifest = _manifest()
    manifest["configuration"] = [
        {"key": f"KEY_{index}", "description": "bounded"} for index in range(33)
    ]

    with pytest.raises(ExtensionContractError, match="extension_configuration_invalid"):
        validate_extension_manifest(manifest)
