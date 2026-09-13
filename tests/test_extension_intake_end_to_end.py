"""MAD-916: scan -> approved install -> persisted inventory readback."""

import json
import shutil
from pathlib import Path

from src.authority_protocol import AuthorityStore
from src.extension_installer import ExtensionLifecycleManager, InlineWebAdapter
from src.extension_registry import ExtensionRegistry, validate_extension_manifest
from src.extension_scan import ExtensionStaticScanner


SOURCE_URL = "https://github.com/example/demo-tools.git"
REVISION = "b" * 40


class _CopyGitClient:
    def __init__(self, source_dir: Path, revision: str = REVISION):
        self.source_dir = source_dir
        self.revision = revision

    def resolve_revision(self, source_url, requested_ref):
        return source_url, requested_ref or "HEAD", self.revision

    def checkout(self, source, ref, revision, destination):
        shutil.copytree(self.source_dir, destination)


def _manifest() -> dict:
    return {
        "protocol_version": "jos-extension.v1",
        "extension_id": "demo-tools",
        "name": "Demo Tools",
        "version": "1.0.0",
        "source": {"url": SOURCE_URL, "revision": "self"},
        "runtime": {"type": "web", "entrypoint": "index.html"},
        "capabilities": {
            "descriptor": {"type": "inline"},
            "schemas": [
                {
                    "name": "run_demo",
                    "description": "Run the demo",
                    "parameters": {
                        "type": "object",
                        "properties": {"target": {"type": "string"}},
                        "required": ["target"],
                        "additionalProperties": False,
                    },
                }
            ],
        },
        "permissions": {"default": "read_only", "capabilities": {}},
        "health": {"type": "catalog", "timeout_seconds": 3},
        "lifecycle": {"install": [], "start": [], "stop": [], "remove": []},
        "data_boundaries": {"read": [], "write": [], "network": []},
        "removal": {"remove_paths": [], "preserve_paths": []},
        "rollback": {"strategy": "pinned_revision", "retain_revisions": 2},
    }


def test_scan_then_approved_install_populates_inventory(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "jarvis-extension.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    (source / "index.html").write_text("<h1>Demo</h1>", encoding="utf-8")
    (source / "LICENSE").write_text("MIT License\n\nPermission is hereby granted...", encoding="utf-8")

    client = _CopyGitClient(source)
    scanner = ExtensionStaticScanner(
        git_client=client,
        staging_root=tmp_path / "managed",
        data_dir=tmp_path / "scans",
    )
    artifact = scanner.run(SOURCE_URL, "HEAD", operator_id="operator")

    assert artifact["repo_class"] == "web_app"
    assert artifact["executed_repo_commands"] == []
    assert artifact["draft_manifest"] is not None
    validate_extension_manifest(artifact["draft_manifest"])

    authority = AuthorityStore(tmp_path / "authority.json")
    registry = ExtensionRegistry(tmp_path / "registry.json")
    manager = ExtensionLifecycleManager(
        root=tmp_path / "extension-root",
        registry=registry,
        authority=authority,
        git_client=client,
        adapters=[InlineWebAdapter()],
    )

    plan = manager.preview_source("install", SOURCE_URL, REVISION, operator_id="operator")
    decision = plan["authority_decision"]
    assert decision["decision"] == "approval_required"
    authority.resolve(
        decision["decision_id"], operator_id="operator", choice="approve", scope="once"
    )
    result = manager.execute_plan(plan["plan_id"], operator_id="operator")

    assert result["result"]["status"] == "succeeded"
    inventory = registry.capability_inventory("demo-tools")
    assert inventory is not None
    assert [item["name"] for item in inventory["capabilities"]] == ["run_demo"]
    assert set(registry.effective_capabilities()) == {"run_demo"}

    snapshot = registry.snapshot()["extensions"]["demo-tools"]
    assert snapshot["enabled"] is True
    assert snapshot["manifest"]["source"]["revision"] == REVISION
