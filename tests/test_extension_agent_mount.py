"""MAD-913: agent capability query and on-demand extension tool mount."""

import asyncio
import json
from pathlib import Path

import pytest

import src.agent_tools.admin_tools as admin_tools
import src.agent_loop as al
import src.extension_agent_mount as extension_agent_mount
import src.extension_registry as extension_registry_module
import src.tool_security as ts
from src.extension_registry import MANIFEST_VERSION, ExtensionRegistry


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


def _register_oracle(registry: ExtensionRegistry, *, tool: str = "inspect_globe") -> str:
    oracle = _manifest("oracle")
    revision = oracle["source"]["revision"]
    registry.register(
        oracle,
        _catalog(oracle, [_tool(tool)], revision),
        source_revision=revision,
        health_available=True,
    )
    return revision


def _mcp_manifest_and_catalog(tool: str = "inspect_crystal") -> tuple[dict, dict, str]:
    manifest = _manifest("atlas")
    manifest["runtime"] = {"type": "mcp", "entrypoint": "server.py"}
    manifest["capabilities"] = {"descriptor": {"type": "mcp", "reference": "quartz-runtime"}}
    manifest["health"] = {"type": "catalog", "timeout_seconds": 5}
    manifest["permissions"] = {"default": "read_only", "capabilities": {}}
    revision = "7" * 40
    return manifest, _catalog(manifest, [_tool(tool)], revision), revision


def _use_registry(monkeypatch, registry: ExtensionRegistry) -> None:
    monkeypatch.setattr(
        extension_registry_module, "ExtensionRegistry", lambda *args, **kwargs: registry
    )


def test_list_and_inspect_report_disabled_capabilities(monkeypatch, tmp_path):
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    _register_oracle(registry)
    registry.disable("oracle")
    _use_registry(monkeypatch, registry)

    listed = asyncio.run(
        admin_tools.do_manage_extensions(json.dumps({"action": "list"}), owner=None)
    )
    assert listed["exit_code"] == 0
    assert listed["count"] == 1
    assert listed["extensions"][0]["id"] == "oracle"
    assert listed["extensions"][0]["enabled"] is False
    assert listed["extensions"][0]["capability_count"] == 1

    inspected = asyncio.run(
        admin_tools.do_manage_extensions(
            json.dumps({"action": "inspect", "extension_id": "oracle"}), owner=None
        )
    )
    assert inspected["exit_code"] == 0
    assert inspected["extension"]["enabled"] is False
    assert inspected["extension"]["inventory_available"] is True
    assert inspected["extension"]["mountable"] == []
    assert inspected["capabilities"] == [
        {
            "name": "inspect_globe",
            "kind": "tool",
            "permission_mode": "read_only",
            "descriptor": "live_catalog",
        }
    ]

    missing = asyncio.run(
        admin_tools.do_manage_extensions(
            json.dumps({"action": "inspect", "extension_id": "nope"}), owner=None
        )
    )
    assert missing["exit_code"] == 1


def test_mount_fails_closed_while_disabled_and_without_mcp_descriptor(monkeypatch, tmp_path):
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    _register_oracle(registry)
    registry.disable("oracle")
    _use_registry(monkeypatch, registry)

    disabled = asyncio.run(
        admin_tools.do_manage_extensions(
            json.dumps({"action": "mount", "names": ["inspect_globe"]}), owner=None
        )
    )
    assert disabled["mounted_extension_tools"] == []
    assert disabled["unavailable"] == [
        {"name": "inspect_globe", "error": "extension_disabled"}
    ]

    _register_oracle(registry)
    engagement = asyncio.run(
        admin_tools.do_manage_extensions(
            json.dumps({"action": "mount", "names": ["inspect_globe"]}), owner=None
        )
    )
    assert engagement["unavailable"] == [
        {"name": "inspect_globe", "error": "extension_mount_requires_engagement"}
    ]

    unknown = asyncio.run(
        admin_tools.do_manage_extensions(
            json.dumps({"action": "mount", "names": ["definitely_missing"]}), owner=None
        )
    )
    assert unknown["unavailable"] == [
        {"name": "definitely_missing", "error": "extension_capability_unknown"}
    ]

    duplicate = asyncio.run(
        admin_tools.do_manage_extensions(
            json.dumps({"action": "mount", "names": ["inspect_globe", "inspect_globe"]}),
            owner=None,
        )
    )
    assert duplicate["exit_code"] == 1


def test_mount_returns_effective_schema_for_enabled_mcp_extension(monkeypatch, tmp_path):
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    manifest, catalog, revision = _mcp_manifest_and_catalog()
    registry.register(manifest, catalog, source_revision=revision, health_available=True)
    _use_registry(monkeypatch, registry)

    result = asyncio.run(
        admin_tools.do_manage_extensions(
            json.dumps({"action": "mount", "names": ["inspect_crystal"]}), owner=None
        )
    )
    assert result["exit_code"] == 0
    assert result["unavailable"] == []
    mounted = result["mounted_extension_tools"]
    assert len(mounted) == 1
    assert mounted[0]["extension_id"] == "atlas"
    assert mounted[0]["permission_mode"] == "read_only"
    assert mounted[0]["descriptor"] == "mcp"
    assert mounted[0]["schema"]["function"]["name"] == "inspect_crystal"


def test_execute_mounted_extension_tool_reconciles_and_fails_closed(monkeypatch):
    manifest, catalog, revision = _mcp_manifest_and_catalog()
    from src.extension_registry import reconcile_extension_catalog

    reconciled = reconcile_extension_catalog(
        manifest, catalog, source_revision=revision, health_available=True
    )
    record = {
        "enabled": False,
        "manifest": reconciled["manifest"],
        "catalog_version": reconciled["catalog_version"],
        "effective_capabilities": reconciled["capabilities"],
        "admitted_skills": [],
        "capability_inventory": None,
    }
    calls: list[tuple] = []

    async def _fake_execute(record_arg, name, arguments, **kwargs):
        calls.append((record_arg, name, dict(arguments)))
        return {"output": "crystal ok", "exit_code": 0}

    import src.extension_mcp_adapter as extension_mcp_adapter

    monkeypatch.setattr(extension_mcp_adapter, "execute_mcp_extension_tool", _fake_execute)
    monkeypatch.setattr(
        extension_agent_mount,
        "ExtensionRegistry",
        lambda *args, **kwargs: type("R", (), {"snapshot": lambda self: {"extensions": {"atlas": record}}})(),
    )

    disabled = asyncio.run(
        extension_agent_mount.execute_mounted_extension_tool(
            {"name": "inspect_crystal", "extension_id": "atlas"}, {"target": "x"}
        )
    )
    assert disabled == {"error": "extension_disabled", "exit_code": 1}
    assert calls == []

    record["enabled"] = True
    ok = asyncio.run(
        extension_agent_mount.execute_mounted_extension_tool(
            {"name": "inspect_crystal", "extension_id": "atlas"}, {"target": "x"}
        )
    )
    assert ok["output"] == "crystal ok"
    assert calls and calls[0][1] == "inspect_crystal"
    assert calls[0][2] == {"target": "x"}


def test_mount_hook_adds_schema_next_round_and_dispatches(monkeypatch):
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: True, raising=False)
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *args, **kwargs: 10, raising=False)

    import src.context_budget as context_budget

    monkeypatch.setattr(
        context_budget, "model_input_token_budget", lambda model: 6000, raising=False
    )

    mounted_schema = {
        "type": "function",
        "function": {
            "name": "inspect_crystal",
            "description": "Inspect a crystal",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
    frames: list[set[str]] = []
    dispatched: list[tuple] = []
    executed: list[str] = []

    async def _fake_stream(_candidates, messages, **kwargs):
        frames.append(
            {
                schema.get("function", {}).get("name")
                for schema in (kwargs.get("tools") or [])
                if schema.get("function", {}).get("name")
            }
        )
        if len(frames) == 1:
            calls = [{
                "id": "call_mount_1",
                "name": "manage_extensions",
                "arguments": json.dumps(
                    {"action": "mount", "names": ["inspect_crystal"]}
                ),
            }]
            yield f"data: {json.dumps({'type': 'tool_calls', 'calls': calls})}\n\n"
        elif len(frames) == 2:
            calls = [{
                "id": "call_use_1",
                "name": "inspect_crystal",
                "arguments": json.dumps({"target": "quartz"}),
            }]
            yield f"data: {json.dumps({'type': 'tool_calls', 'calls': calls})}\n\n"
        else:
            yield 'data: {"delta": "Done."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block.tool_type)
        if block.tool_type == "manage_extensions":
            return "manage_extensions", {
                "mounted_extension_tools": [
                    {
                        "name": "inspect_crystal",
                        "extension_id": "atlas",
                        "permission_mode": "read_only",
                        "descriptor": "mcp",
                        "schema": mounted_schema,
                    }
                ],
                "unavailable": [],
                "exit_code": 0,
            }
        return block.tool_type, {"output": "unexpected", "exit_code": 0}

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    async def _fake_mounted_execute(spec, arguments):
        dispatched.append((dict(spec), dict(arguments)))
        return {"output": "crystal ok", "exit_code": 0}

    monkeypatch.setattr(
        extension_agent_mount, "execute_mounted_extension_tool", _fake_mounted_execute
    )

    async def _collect():
        return [
            chunk
            async for chunk in al.stream_agent_loop(
                "https://openrouter.ai/api/v1/chat/completions",
                "deepseek-v4-flash",
                [{"role": "user", "content": "what can the atlas plugin do?"}],
                max_rounds=4,
                relevant_tools={"web_search"},
                owner="leo",
                workspace="/tmp",
                context_length=4000,
            )
        ]

    asyncio.run(_collect())

    assert "manage_extensions" in executed, executed
    assert len(frames) >= 3, frames
    assert "inspect_crystal" not in frames[0], sorted(frames[0])
    assert "inspect_crystal" in frames[1], sorted(frames[1])
    assert dispatched, "mounted extension tool was not dispatched"
    assert dispatched[0][0]["extension_id"] == "atlas"
    assert dispatched[0][1] == {"target": "quartz"}