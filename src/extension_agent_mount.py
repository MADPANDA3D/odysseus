"""MAD-913: agent-facing extension discovery, inspection, and per-turn mount.

The inventory is advisory. Mounting copies an already-effective schema into the
current request's tool selection; every call still runs through the existing
extension MCP adapter with its live reconciliation, so the registry's enabled
state and the qualified MCP catalog remain the only execution authority.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.extension_capability_inventory import (
    advisory_capability_items,
    resolve_mount_schemas,
)
from src.extension_registry import ExtensionContractError, ExtensionRegistry

MAX_EXTENSION_MOUNTS = 32
# Only MCP-descriptor tools have a browserless per-call execution path today.
# Web/live-catalog capabilities require the engaged surface/result bridge and
# are refused at mount time instead of fabricating a callback.
MOUNTABLE_DESCRIPTORS = frozenset({"mcp"})


def extension_catalog_rows(registry: ExtensionRegistry) -> list[dict[str, Any]]:
    """Sanitized installed-extension rows; works while an extension is disabled."""
    rows: list[dict[str, Any]] = []
    for extension_id, record in sorted(registry.snapshot()["extensions"].items()):
        inventory = record.get("capability_inventory")
        items = advisory_capability_items(inventory) if inventory else []
        by_kind: dict[str, int] = {}
        for item in items:
            by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        rows.append({
            "id": extension_id,
            "name": record["manifest"]["name"],
            "enabled": bool(record["enabled"]),
            "runtime": record["manifest"]["runtime"]["type"],
            "capability_count": len(items),
            "capabilities_by_kind": by_kind,
        })
    return rows


def inspect_extension(
    registry: ExtensionRegistry, extension_id: str
) -> dict[str, Any] | None:
    """Advisory capability detail for one installed extension, or None."""
    record = registry.snapshot()["extensions"].get(extension_id)
    if record is None:
        return None
    inventory = record.get("capability_inventory")
    items = advisory_capability_items(inventory) if inventory else []
    descriptor = record["manifest"]["capabilities"]["descriptor"]["type"]
    return {
        "id": extension_id,
        "name": record["manifest"]["name"],
        "version": record["manifest"]["version"],
        "enabled": bool(record["enabled"]),
        "runtime": record["manifest"]["runtime"]["type"],
        "descriptor": descriptor,
        "inventory_available": inventory is not None,
        "mountable": sorted(
            item["name"]
            for item in items
            if item["kind"] == "tool" and descriptor in MOUNTABLE_DESCRIPTORS
        ),
        "capabilities": items,
    }


def mount_extension_capabilities(
    registry: ExtensionRegistry, names: Iterable[str]
) -> dict[str, Any]:
    """Resolve mountable extension capabilities without enabling extensions.

    Returns ``{"mounted": [...], "unavailable": [...]}``. Each mounted entry
    carries the exact effective schema; nothing here executes.
    """
    requested = [str(name).strip() for name in names if str(name).strip()]
    if (
        not requested
        or len(requested) > MAX_EXTENSION_MOUNTS
        or len(requested) != len(set(requested))
    ):
        raise ExtensionContractError("extension_capability_mount_invalid")
    snapshot = registry.snapshot()["extensions"]
    mounted: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    for name in requested:
        match: tuple[str, Mapping[str, Any], Mapping[str, Any]] | None = None
        for extension_id, record in snapshot.items():
            inventory = record.get("capability_inventory")
            if not inventory:
                continue
            inventory_names = {
                item["name"] for item in inventory.get("capabilities", [])
            }
            if name in inventory_names:
                match = (extension_id, record, inventory)
                break
        if match is None:
            unavailable.append({"name": name, "error": "extension_capability_unknown"})
            continue
        extension_id, record, inventory = match
        try:
            resolve_mount_schemas(inventory, [name])
        except ExtensionContractError as exc:
            unavailable.append({"name": name, "error": exc.code})
            continue
        if not record.get("enabled"):
            unavailable.append({"name": name, "error": "extension_disabled"})
            continue
        descriptor = record["manifest"]["capabilities"]["descriptor"]["type"]
        if descriptor not in MOUNTABLE_DESCRIPTORS:
            unavailable.append({
                "name": name,
                "error": "extension_mount_requires_engagement",
            })
            continue
        capability = next(
            (
                item
                for item in record.get("effective_capabilities", [])
                if item["name"] == name
            ),
            None,
        )
        if capability is None:
            unavailable.append({
                "name": name,
                "error": "extension_capability_unavailable",
            })
            continue
        mounted.append({
            "name": name,
            "extension_id": extension_id,
            "permission_mode": capability["permission_mode"],
            "descriptor": descriptor,
            "schema": capability["schema"],
        })
    return {"mounted": mounted, "unavailable": unavailable}


async def execute_mounted_extension_tool(
    spec: Mapping[str, Any], arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Execute one mounted MCP extension capability through the native adapter."""
    from src.extension_mcp_adapter import execute_mcp_extension_tool

    name = str(spec.get("name") or "").strip()
    extension_id = str(spec.get("extension_id") or "").strip()
    if not name or not extension_id or not isinstance(arguments, Mapping):
        return {"error": "extension_mcp_capability_unavailable", "exit_code": 1}
    record = ExtensionRegistry().snapshot()["extensions"].get(extension_id)
    if record is None or not record.get("enabled"):
        return {"error": "extension_disabled", "exit_code": 1}
    return await execute_mcp_extension_tool(record, name, dict(arguments))
