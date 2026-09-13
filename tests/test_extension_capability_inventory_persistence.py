"""MAD-912: revision-bound capability inventory survives disable and restart."""

import json
from pathlib import Path

from src.extension_capability_inventory import (
    build_capability_inventory,
    validate_capability_inventory,
)
from src.extension_registry import (
    MANIFEST_VERSION,
    ExtensionRegistry,
    reconcile_extension_catalog,
)


FIXTURES = Path(__file__).parent / "fixtures" / "extensions"


def _manifest(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.manifest.json").read_text(encoding="utf-8"))


def _tool(name: str) -> dict:
    return {
        "name": name,
        "description": f"Run {name}",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
    }


def _catalog(manifest: dict, tools: list[dict], revision: str) -> dict:
    return {
        "protocol_version": MANIFEST_VERSION,
        "extension_id": manifest["extension_id"],
        "version": manifest["version"],
        "source_revision": revision,
        "tools": tools,
    }


def _register_oracle(registry: ExtensionRegistry) -> str:
    oracle = _manifest("oracle")
    revision = oracle["source"]["revision"]
    registry.register(
        oracle,
        _catalog(oracle, [_tool("inspect_globe")], revision),
        source_revision=revision,
        health_available=True,
    )
    return revision


def test_disabled_extension_keeps_revision_bound_inventory(tmp_path):
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    revision = _register_oracle(registry)

    enabled_record = registry.snapshot()["extensions"]["oracle"]
    assert enabled_record["capability_inventory"]["source_revision"] == revision
    assert [item["name"] for item in enabled_record["capability_inventory"]["capabilities"]] == [
        "inspect_globe"
    ]

    assert registry.disable("oracle") is True
    disabled_record = registry.snapshot()["extensions"]["oracle"]

    assert disabled_record["enabled"] is False
    assert disabled_record["effective_capabilities"] == []
    assert disabled_record["admitted_skills"] == []
    assert disabled_record["capability_inventory"]["source_revision"] == revision
    assert validate_capability_inventory(disabled_record["capability_inventory"])

    inventory = registry.capability_inventory("oracle")
    assert inventory == disabled_record["capability_inventory"]

    assert registry.effective_capabilities() == {}
    assert registry.context_extensions({"oracle"}) == {}


def test_restart_rehydrates_inventory_without_running_adapters(tmp_path):
    path = tmp_path / "extensions.json"
    registry = ExtensionRegistry(path)
    _register_oracle(registry)
    registry.disable("oracle")

    restarted = ExtensionRegistry(path)

    inventory = restarted.capability_inventory("oracle")
    assert inventory is not None
    assert inventory["extension_id"] == "oracle"
    assert [item["name"] for item in inventory["capabilities"]] == ["inspect_globe"]
    assert restarted.effective_capabilities() == {}


def test_tampered_or_stale_inventory_fails_closed_without_touching_authority(tmp_path):
    path = tmp_path / "extensions.json"
    registry = ExtensionRegistry(path)
    _register_oracle(registry)

    state = json.loads(path.read_text(encoding="utf-8"))
    state["extensions"]["oracle"]["capability_inventory"]["capabilities"][0][
        "permission_mode"
    ] = "destructive"
    path.write_text(json.dumps(state), encoding="utf-8")

    reopened = ExtensionRegistry(path)
    assert reopened.capability_inventory("oracle") is None
    assert "inspect_globe" in reopened.effective_capabilities()

    oracle = _manifest("oracle")
    real_revision = oracle["source"]["revision"]
    reconciled = reconcile_extension_catalog(
        oracle,
        _catalog(oracle, [_tool("inspect_globe")], real_revision),
        source_revision=real_revision,
        health_available=True,
    )
    stale_state = json.loads(path.read_text(encoding="utf-8"))
    stale_state["extensions"]["oracle"]["capability_inventory"] = build_capability_inventory(
        reconciled, source_revision="9" * 40
    )
    path.write_text(json.dumps(stale_state), encoding="utf-8")

    stale_registry = ExtensionRegistry(path)
    assert stale_registry.capability_inventory("oracle") is None
    assert "inspect_globe" in stale_registry.effective_capabilities()


def test_legacy_enabled_record_backfills_inventory_without_adapters(tmp_path):
    path = tmp_path / "extensions.json"
    registry = ExtensionRegistry(path)
    _register_oracle(registry)
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["extensions"]["oracle"]["capability_inventory"]
    path.write_text(json.dumps(state), encoding="utf-8")

    reopened = ExtensionRegistry(path)

    inventory = reopened.capability_inventory("oracle")
    assert inventory is not None
    assert validate_capability_inventory(inventory) == inventory
    assert [item["name"] for item in inventory["capabilities"]] == ["inspect_globe"]
    assert "inspect_globe" in reopened.effective_capabilities()


def test_legacy_disabled_record_cannot_backfill_without_metadata(tmp_path):
    path = tmp_path / "extensions.json"
    registry = ExtensionRegistry(path)
    _register_oracle(registry)
    registry.disable("oracle")
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["extensions"]["oracle"]["capability_inventory"]
    path.write_text(json.dumps(state), encoding="utf-8")

    reopened = ExtensionRegistry(path)

    assert reopened.capability_inventory("oracle") is None
    assert reopened.effective_capabilities() == {}


def test_skill_bundle_inventory_survives_disable(tmp_path):
    manifest = _manifest("atlas")
    manifest["runtime"] = {"type": "skills", "entrypoint": "skills"}
    manifest["capabilities"] = {
        "descriptor": {"type": "skill_bundle", "format": "agent_skill", "include": ["pdf-tools"]}
    }
    manifest["permissions"] = {"default": "read_only", "capabilities": {"pdf-tools": "read_only"}}
    revision = "4" * 40
    catalog = {
        "protocol_version": MANIFEST_VERSION,
        "extension_id": manifest["extension_id"],
        "version": manifest["version"],
        "source_revision": revision,
        "skills": [
            {
                "id": "pdf-tools",
                "source_path": "skills/pdf-tools/SKILL.md",
                "owner_scope": "extension:atlas",
                "platforms": ["linux"],
                "requires_toolsets": [],
            }
        ],
    }
    reconciled = reconcile_extension_catalog(
        manifest, catalog, source_revision=revision, health_available=True
    )
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    registry.register(manifest, catalog, source_revision=revision, health_available=True)
    registry.disable("atlas")

    inventory = registry.capability_inventory("atlas")
    assert [item["kind"] for item in inventory["capabilities"]] == ["skill"]
    assert inventory["capabilities"][0]["skill"]["source_path"] == "skills/pdf-tools/SKILL.md"
    assert reconciled["admitted_skills"][0]["id"] == "pdf-tools"
    assert registry.effective_capabilities() == {}