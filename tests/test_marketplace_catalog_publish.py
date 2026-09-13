"""MAD-941: marketplace catalog publishing tooling."""

import importlib.util
import json
import shutil
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.marketplace_catalog import (
    MarketplaceCatalogError,
    validate_published_catalog,
    verify_catalog_artifact,
)


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_marketplace_catalog.py"
_module_spec = importlib.util.spec_from_file_location("build_marketplace_catalog", SCRIPT)
publisher = importlib.util.module_from_spec(_module_spec)
_module_spec.loader.exec_module(publisher)

SOURCE_URL = "https://github.com/example/demo-tools.git"
REVISION = "b" * 40


def _write_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "jarvis-extension.json").write_text(
        json.dumps({
            "protocol_version": "jos-extension.v1",
            "extension_id": "demo-tools",
            "name": "Demo Tools",
            "version": "1.0.0",
            "source": {"url": SOURCE_URL, "revision": "self"},
            "runtime": {"type": "web", "entrypoint": "index.html"},
            "capabilities": {"descriptor": {"type": "inline"}, "schemas": [{
                "name": "run_demo",
                "description": "Run the demo",
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            }]},
            "permissions": {"default": "read_only", "capabilities": {}},
            "health": {"type": "catalog", "timeout_seconds": 5},
            "lifecycle": {"install": [], "start": [], "stop": [], "remove": []},
            "data_boundaries": {"read": [], "write": [], "network": []},
            "removal": {"remove_paths": [], "preserve_paths": []},
            "rollback": {"strategy": "pinned_revision", "retain_revisions": 1},
            "configuration": [{"key": "DEMO_TOKEN", "description": "Owner token", "required": True, "secret": True}],
        }),
        encoding="utf-8",
    )
    (repo / "index.html").write_text("<h1>demo</h1>", encoding="utf-8")
    return repo


def _fake_git(repo: Path, revision: str):
    def _fake(argv, *, cwd=None):
        if argv[:2] == ["git", "clone"]:
            shutil.copytree(repo, Path(argv[-1]))
            return ""
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return revision
        if argv[:2] == ["git", "archive"]:
            output = Path(argv[argv.index("-o") + 1])
            with tarfile.open(output, "w:gz") as archive:
                archive.add(repo, arcname="demo-tools-1.0.0")
            return ""
        raise AssertionError(argv)
    return _fake


def _spec() -> dict:
    return {
        "source_url": SOURCE_URL,
        "ref": "v1.0.0",
        "summary": "Demo tools package",
        "categories": ["developer-tools"],
        "license": "MIT",
        "publisher": {"id": "madpanda3d", "name": "MADPANDA3D", "url": "https://github.com/MADPANDA3D"},
        "compatibility": {
            "pandamonium_min": "1.0.0",
            "pandamonium_max": "1.99.99",
            "platforms": ["linux"],
            "architectures": ["amd64"],
        },
    }


def _key(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "signing.pem"
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    return key, path


def _build(tmp_path, monkeypatch, specs=None):
    repo = _write_repo(tmp_path)
    monkeypatch.setattr(publisher, "_run", _fake_git(repo, REVISION))
    _key_obj, pem = _key(tmp_path)
    private_key = publisher.load_private_key(pem)
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    workdir = tmp_path / "work"
    workdir.mkdir()
    catalog, trusted, artifacts = publisher.build_from_specs(
        specs or [_spec()],
        private_key=private_key,
        key_id="test-key",
        artifact_base_url="https://example.com/marketplace",
        catalog_id="test-catalog",
        generated_at=now.isoformat(),
        expires_at=(now + timedelta(days=90)).isoformat(),
        workdir=workdir,
    )
    out_dir = tmp_path / "out"
    publisher.write_outputs(out_dir, catalog, trusted)
    for artifact in artifacts:
        shutil.copy2(artifact["path"], out_dir / artifact["filename"])
    return catalog, trusted, artifacts, out_dir


def test_catalog_builds_validates_and_verifies(tmp_path, monkeypatch):
    catalog, trusted, artifacts, out_dir = _build(tmp_path, monkeypatch)

    assert (out_dir / "catalog.json").is_file()
    assert (out_dir / "trusted_keys.json").is_file()
    validated = validate_published_catalog(
        catalog, trusted_keys=trusted, now=datetime(2026, 9, 14, tzinfo=timezone.utc)
    )
    assert len(validated["entries"]) == 1
    entry = validated["entries"][0]
    assert entry["manifest"]["extension_id"] == "demo-tools"
    assert entry["artifact"]["sha256"] == artifacts[0]["sha256"]
    assert entry["artifact"]["size_bytes"] == artifacts[0]["size_bytes"]
    assert entry["artifact"]["url"].endswith(artifacts[0]["filename"])
    assert entry["configuration"] == [
        {"key": "DEMO_TOKEN", "description": "Owner token", "required": True, "secret": True}
    ]
    assert entry["publisher"]["key_id"] == "test-key"

    content = (out_dir / artifacts[0]["filename"]).read_bytes()
    assert verify_catalog_artifact(entry["artifact"], content) is True


def test_tampered_catalog_and_artifact_fail_closed(tmp_path, monkeypatch):
    catalog, trusted, artifacts, out_dir = _build(tmp_path, monkeypatch)
    entry = catalog["entries"][0]

    tampered = json.loads(json.dumps(catalog))
    tampered["entries"][0]["summary"] = "tampered"
    with pytest.raises(MarketplaceCatalogError):
        validate_published_catalog(
            tampered, trusted_keys=trusted, now=datetime(2026, 9, 14, tzinfo=timezone.utc)
        )

    assert verify_catalog_artifact(
        entry["artifact"], (out_dir / artifacts[0]["filename"]).read_bytes()
    ) is True
    with pytest.raises(MarketplaceCatalogError):
        verify_catalog_artifact(entry["artifact"], b"not the artifact")


@pytest.mark.parametrize(
    "specs",
    [
        [],
        [{"source_url": "http://insecure.example/repo.git"}],
        [{"source_url": SOURCE_URL}],
    ],
)
def test_bad_package_specs_fail_closed(specs):
    with pytest.raises(SystemExit):
        publisher.validate_package_specs(specs)


def test_duplicate_package_ids_fail(tmp_path, monkeypatch):
    first = _spec()
    second = _spec()
    second["ref"] = "v1.0.1"
    with pytest.raises(SystemExit):
        publisher.validate_package_specs([{**first, "extension_id": "demo-tools"}, {**second, "extension_id": "demo-tools"}])