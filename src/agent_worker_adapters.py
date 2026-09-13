from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

import httpx

from src.agent_identity import configured_agent_name
from src.model_discovery import installation_capabilities

MILESTONE_MARKER = "[[ODYSSEUS_MILESTONE]]"
WORKER_IDS = ("pc-codex", "hermes", "vps-codex")
_WORKSPACE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CODEX_BRIDGE_PROTOCOL = "pandamonium.codex-bridge.v2"
AGENT_ENDPOINT_KIND = "agent"
LEGACY_CODEX_ENDPOINT_ID = "pc-codex"
AGENT_BRIDGE_PROTOCOLS = ("codex-bridge",)


class WorkerUnavailable(RuntimeError):
    """Stable, non-sensitive failure raised at the public broker boundary."""


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _worker_label(name: str, default: str) -> str:
    """Return a bounded installation-owned label for a fixed adapter slot."""
    value = str(os.getenv(name) or default).strip()
    value = " ".join(value.split())
    return value[:80] or default


def _display_name(value: object, fallback: str) -> str:
    return " ".join(str(value or fallback).split())[:80] or fallback


def configured_worker_workspaces() -> dict[str, list[str]]:
    """Return installation-owned workspace aliases; public defaults are empty."""
    raw = os.getenv("ODYSSEUS_WORKER_WORKSPACES_JSON", "").strip()
    if not raw:
        return {worker: [] for worker in WORKER_IDS}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid_worker_workspace_configuration") from exc
    if not isinstance(payload, dict) or any(worker not in WORKER_IDS for worker in payload):
        raise RuntimeError("invalid_worker_workspace_configuration")
    configured = {worker: [] for worker in WORKER_IDS}
    for worker, values in payload.items():
        if (
            not isinstance(values, list)
            or len(values) > 32
            or any(not isinstance(value, str) or not _WORKSPACE_NAME.fullmatch(value) for value in values)
        ):
            raise RuntimeError("invalid_worker_workspace_configuration")
        configured[worker] = list(dict.fromkeys(values))
    return configured


def agent_meta(row: Any) -> dict[str, Any]:
    """Return the parsed agent metadata blob for a node endpoint row."""
    value = getattr(row, "agent_meta", None)
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def agent_endpoint_workspaces(row: Any) -> list[str]:
    """Return the allowlisted workspaces a registered node endpoint exposes."""
    values = agent_meta(row).get("workspaces")
    if not isinstance(values, list):
        return []
    workspaces: list[str] = []
    for value in values[:32]:
        text = str(value or "").strip()
        if _WORKSPACE_NAME.fullmatch(text) and text not in workspaces:
            workspaces.append(text)
    return workspaces


def agent_endpoint_protocol(row: Any) -> str:
    """Return the bridge protocol a registered node endpoint was paired with."""
    protocol = " ".join(str(agent_meta(row).get("protocol") or "").split())[:40]
    return protocol if protocol in AGENT_BRIDGE_PROTOCOLS else AGENT_BRIDGE_PROTOCOLS[0]


def agent_worker_id(endpoint_id: str) -> str:
    """Map a node endpoint row id to its stable worker id.

    The migrated legacy Codex binding keeps the historical ``pc-codex`` worker
    id so existing voice/chat bindings keep resolving; every other registered
    node gets an ``agent-`` prefixed id that cannot collide with fixed workers.
    """
    value = str(endpoint_id or "").strip()
    if value == LEGACY_CODEX_ENDPOINT_ID:
        return LEGACY_CODEX_ENDPOINT_ID
    safe = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")[:57]
    return f"agent-{safe or 'node'}"


def _token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _health_failure(exc: Exception) -> dict[str, str]:
    """Return a stable, non-sensitive connection failure classification."""
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    error_code = str(exc) if isinstance(exc, RuntimeError) else ""
    if error_code.endswith("_token_missing"):
        return {"state": "auth_required", "reason": "token_missing"}
    if status_code in {401, 403}:
        return {"state": "auth_required", "reason": "authentication_failed"}
    return {"state": "unreachable", "reason": "connection_failed"}


def _hermes_run_features(features: dict[str, Any]) -> dict[str, bool]:
    """Normalize Hermes capability names across compatible API revisions."""
    return {
        "runs": bool(features.get("run_submission") and features.get("run_events_sse")),
        "stop": bool(features.get("run_stop")),
        "approvals": bool(features.get("run_approval_response") or features.get("run_approval")),
    }


def require_worker_task_permission(permission_mode: str, approved: bool) -> None:
    is_approved = approved is True
    if permission_mode == "read_only" and not is_approved:
        return
    if permission_mode == "workspace_write" and _enabled(
        "ODYSSEUS_PRIVATE_WORKER_MUTATIONS", False
    ):
        if not is_approved:
            raise PermissionError("approval_required")
        return
    raise PermissionError("public_tasks_read_only")


def require_read_only(permission_mode: str, approved: bool) -> None:
    """Reject mutation and caller pre-approval on the public Voice Orb API."""
    if permission_mode == "read_only" and approved is False:
        return
    raise PermissionError("public_tasks_read_only")


def _validated_approval_choice(task: dict[str, Any], payload: dict[str, Any]) -> str:
    permission_mode = str(task.get("permission_mode") or "read_only")
    require_worker_task_permission(permission_mode, task.get("approved") is True)
    choice = str(payload.get("choice") or "deny")
    if choice not in {"once", "session", "always", "deny"}:
        raise ValueError("invalid_approval_choice")
    if permission_mode == "read_only" and choice != "deny":
        raise PermissionError("read_only_task_approval_must_deny")
    return choice


def _require_worker_task_permission(task: dict[str, Any]) -> None:
    require_worker_task_permission(
        str(task.get("permission_mode") or ""),
        task.get("approved") is True,
    )


def _last_remote_event_id(task: dict[str, Any]) -> str:
    for event in reversed(task.get("events") or []):
        metadata = event.get("metadata") or {}
        remote_event_id = str(metadata.get("remote_event_id") or "")
        if remote_event_id:
            return remote_event_id
    return ""


def _hermes_instructions(task: dict[str, Any]) -> str:
    operator = str(task.get("owner") or "the authenticated operator")
    base = (
        f"You are the selected Hermes worker operating for {operator} through {configured_agent_name()} and "
        "Pandamonium. Give factual milestone updates and a clear final result. "
        "Never claim an action completed without tool evidence. "
        "Only after a subtask is complete and verified by tool evidence, you may emit one reasoning update as "
        f"{MILESTONE_MARKER} <one completed-subtask update>. Do not use that marker for plans, activity, "
        "commands, estimates, or the final result. "
    )
    if (
        _enabled("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", False)
        and task.get("permission_mode") == "workspace_write"
        and task.get("approved") is True
    ):
        return base + (
            "Pandamonium approved this task at the broker level. You may attempt only the specifically requested "
            "mutation using normal Hermes tools. Do not bypass or suppress Hermes' native tool approval gate; "
            "no other side effects are authorized."
        )
    return base + (
        "This run is read-only. Do not attempt file changes, installs, deletes, service operations, or other side effects."
    )


class WorkerAdapter(Protocol):
    worker: str
    enabled: bool

    async def start(self, task: dict[str, Any]) -> dict[str, Any]: ...
    async def status(self, task: dict[str, Any]) -> dict[str, Any]: ...
    async def events(self, task: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...
    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...
    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...
    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...
    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]: ...
    async def health(self) -> dict[str, Any]: ...


class CodexBridgeAdapter:
    adapter_name = "codex-bridge"

    def __init__(
        self,
        worker: str,
        url: str,
        token_file: Path | None,
        *,
        enabled: bool,
        machine: str,
        label: str | None = None,
        workspaces: list[str] | None = None,
        token: str | None = None,
    ):
        self.worker = worker
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.enabled = enabled
        self.machine = machine
        self.label = label or ("PC Codex" if worker == "pc-codex" else "VPS Codex")
        self.configured_workspaces = list(workspaces or [])
        # Registered node endpoints carry their pairing token in the row; the
        # fixed env-configured workers keep reading it from disk.
        self.token_value = str(token or "")

    def _token(self) -> str:
        if self.token_value:
            return self.token_value
        return _token(self.token_file) if self.token_file else ""

    def _headers(self) -> dict[str, str]:
        token = self._token()
        if not token:
            raise RuntimeError(f"{self.worker}_token_missing")
        return {"Authorization": f"Bearer {token}"}

    async def start(self, task: dict[str, Any]) -> dict[str, Any]:
        _require_worker_task_permission(task)
        payload = {
            "session_id": task["session_id"],
            "workspace": task["workspace"],
            "prompt": task["prompt"],
            "permission_mode": task["permission_mode"],
            "approved": task.get("approved", False),
            "codex_thread_id": task.get("codex_thread_id"),
            "thread_title": task.get("thread_title"),
            "request_id": task.get("request_id"),
            "codex_model": task.get("codex_model"),
            "codex_reasoning_effort": task.get("codex_reasoning_effort"),
            **({"images": task["images"]} if task.get("images") else {}),
            "preserve_native_config": task.get("preserve_native_config") is True,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.url}/v1/tasks", json=payload, headers=self._headers())
        response.raise_for_status()
        remote = response.json()
        return {
            "remote_task_id": remote["task_id"],
            "status": remote.get("status", "queued"),
            "codex_thread_id": remote.get("codex_thread_id"),
        }

    async def status(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.url}/v1/tasks/{task['remote_task_id']}",
                headers=self._headers(),
            )
        response.raise_for_status()
        return response.json()

    async def catalog_models(self) -> dict[str, Any]:
        if not self.enabled:
            raise WorkerUnavailable("codex_bridge_not_configured")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.url}/v1/catalog/models", headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def catalog_task_details(self, project_id: str, thread_id: str, *, history: bool = False,
                                   cursor: str | None = None, limit: int = 5) -> dict[str, Any]:
        if not self.enabled:
            raise WorkerUnavailable("codex_bridge_not_configured")
        if not _WORKSPACE_NAME.fullmatch(project_id) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", thread_id):
            raise ValueError("invalid_codex_task")
        suffix = "/history" if history else ""
        params = {"limit": limit, **({"cursor": cursor} if cursor else {})} if history else {}
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(f"{self.url}/v1/catalog/projects/{project_id}/tasks/{thread_id}{suffix}",
                                       params=params, headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def catalog_projects(
        self,
        *,
        query: str = "",
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise WorkerUnavailable("codex_bridge_not_configured")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.url}/v1/catalog/projects",
                params={"query": query, "cursor": cursor, "limit": limit},
                headers=self._headers(),
            )
        response.raise_for_status()
        return response.json()

    async def catalog_tasks(
        self,
        project_id: str,
        *,
        query: str = "",
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise WorkerUnavailable("codex_bridge_not_configured")
        if not _WORKSPACE_NAME.fullmatch(project_id):
            raise ValueError("invalid_project_id")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.url}/v1/catalog/projects/{project_id}/tasks",
                params={"query": query, "cursor": cursor, "limit": limit},
                headers=self._headers(),
            )
        response.raise_for_status()
        return response.json()

    async def events(self, task: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        headers = self._headers()
        last_event_id = _last_remote_event_id(task)
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET",
                f"{self.url}/v1/tasks/{task['remote_task_id']}/events",
                params={"after": -1},
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    event.pop("seq", None)
                    event["worker"] = self.worker
                    metadata = dict(event.get("metadata") or {})
                    remote_event_id = str(event.get("event_id") or "")
                    if remote_event_id:
                        metadata["remote_event_id"] = remote_event_id
                    thread_id = metadata.get("codex_thread_id")
                    if thread_id:
                        metadata["codex_deep_link"] = f"codex://threads/{thread_id}"
                    event["metadata"] = metadata
                    yield event

    async def _action(self, task: dict[str, Any], action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.url}/v1/tasks/{task['remote_task_id']}/{action}",
                json=payload or {},
                headers=self._headers(),
            )
        response.raise_for_status()
        return response.json()

    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "reply", payload)

    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "steer", payload)

    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "choice": _validated_approval_choice(task, payload)}
        return await self._action(task, "approval", payload)

    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "cancel")

    async def health(self) -> dict[str, Any]:
        try:
            self._headers()
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.url}/health")
            response.raise_for_status()
            payload = response.json()
            payload = payload if isinstance(payload, dict) else {}
            if payload.get("ok") is False or payload.get("app_server") is False:
                return {
                    "state": "unreachable",
                    "reason": "codex_binary_not_found" if payload.get("reason") == "codex_binary_not_found" else "codex_unavailable",
                    "machine": self.machine,
                    "protocol": "codex-bridge",
                    "protocol_ready": False,
                }
            features = payload.get("features") if isinstance(payload.get("features"), dict) else {}
            protocol_ready = (
                payload.get("protocol_version") == CODEX_BRIDGE_PROTOCOL
                and features.get("project_catalog") is True
                and features.get("task_control") is True
            )
            if not protocol_ready:
                return {
                    "state": "incompatible",
                    "reason": "bridge_update_required",
                    "machine": self.machine,
                    "protocol": "codex-bridge",
                    "protocol_ready": False,
                }
            installation = (
                payload.get("installation")
                if isinstance(payload.get("installation"), dict)
                else {}
            )
            display_name = _display_name(installation.get("display_name"), self.label)
            if display_name == self.worker:
                display_name = self.label
            return {
                "state": "connected",
                "machine": self.machine,
                "protocol": "codex-bridge",
                "protocol_ready": True,
                "display_name": display_name,
                "installation_capabilities": ["codex"],
            }
        except Exception as exc:
            return {"machine": self.machine, **_health_failure(exc)}


class HermesRunsAdapter:
    worker = "hermes"
    adapter_name = "hermes-runs"

    def __init__(
        self,
        url: str,
        token_file: Path,
        *,
        enabled: bool,
        label: str = "Hermes",
        workspaces: list[str] | None = None,
    ):
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.enabled = enabled
        self.machine = "Hermes laptop"
        self.label = label
        self.configured_workspaces = list(workspaces or [])

    def _headers(self, task: dict[str, Any] | None = None) -> dict[str, str]:
        token = _token(self.token_file)
        if not token:
            raise RuntimeError("hermes_token_missing")
        headers = {"Authorization": f"Bearer {token}"}
        if task and task.get("worker_session_key"):
            headers["X-Hermes-Session-Key"] = task["worker_session_key"]
        return headers

    async def direct_chat(
        self,
        *,
        session_id: str,
        session_key: str,
        message: str,
        images: list[dict] | None = None,
    ) -> str:
        """Run one persistent foreground turn through Gordon's native agent."""
        if not self.enabled:
            raise RuntimeError("hermes_not_connected")
        headers = self._headers()
        headers["X-Hermes-Session-Id"] = session_id[:256]
        headers["X-Hermes-Session-Key"] = session_key[:256]
        payload = {
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": message},
                *[{"type": "image_url", "image_url": {"url": image["url"]}} for image in images],
            ] if images else message}],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(
                f"{self.url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        content = (
            choices[0].get("message", {}).get("content")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else ""
        )
        reply = re.sub(
            r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>",
            "",
            str(content or ""),
            flags=re.IGNORECASE,
        ).strip()
        if not reply:
            raise RuntimeError("hermes_direct_chat_empty")
        return reply

    async def start(self, task: dict[str, Any]) -> dict[str, Any]:
        _require_worker_task_permission(task)
        session_key = task.get("worker_session_key") or f"odysseus:{task['session_id']}:{task['workspace']}"
        task["worker_session_key"] = session_key[:256]
        payload = {
            "input": task["prompt"],
            "session_id": session_key,
            "instructions": _hermes_instructions(task),
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.url}/v1/runs", json=payload, headers=self._headers(task))
        response.raise_for_status()
        remote = response.json()
        return {"remote_task_id": remote["run_id"], "status": "queued", "worker_session_key": session_key}

    async def status(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.url}/v1/runs/{task['remote_task_id']}",
                headers=self._headers(task),
            )
        response.raise_for_status()
        return response.json()

    async def events(self, task: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        headers = self._headers(task)
        last_event_id = _last_remote_event_id(task)
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET",
                f"{self.url}/v1/runs/{task['remote_task_id']}/events",
                headers=headers,
            ) as response:
                response.raise_for_status()
                sse_event_id = ""
                async for line in response.aiter_lines():
                    if line.startswith("id:"):
                        sse_event_id = line[3:].strip()
                        continue
                    if not line.startswith("data: "):
                        continue
                    raw = json.loads(line[6:])
                    event = self._normalize(raw)
                    if event:
                        remote_event_id = str(
                            sse_event_id or raw.get("event_id") or raw.get("id") or ""
                        )
                        metadata = dict(event.get("metadata") or {})
                        if remote_event_id:
                            metadata["remote_event_id"] = remote_event_id
                        event["metadata"] = metadata
                        event["event_id"] = remote_event_id or str(uuid.uuid4())
                        yield event
                    sse_event_id = ""

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        kind = str(raw.get("event") or "")
        if kind == "tool.started":
            return {"type": "tool_activity", "text": f"Hermes started {raw.get('tool') or 'a tool'}.", "metadata": raw}
        if kind == "tool.completed":
            return {"type": "tool_activity", "text": f"Hermes completed {raw.get('tool') or 'a tool'}.", "metadata": raw}
        if kind == "reasoning.available" and raw.get("text"):
            text = str(raw["text"]).strip()
            remainder = text[len(MILESTONE_MARKER):] if text.startswith(MILESTONE_MARKER) else None
            milestone = remainder is not None and (not remainder or remainder[0].isspace())
            if milestone:
                text = remainder.strip()
            if not text:
                return None
            metadata = {"source_event": kind}
            if milestone:
                metadata["milestone"] = True
            return {"type": "progress", "text": text, "metadata": metadata}
        if kind == "approval.request":
            text = str(raw.get("description") or "Hermes needs approval before continuing.")
            return {"type": "approval_required", "text": text, "metadata": raw}
        if kind == "run.completed":
            return {"type": "result", "text": str(raw.get("output") or "Hermes completed the run."), "metadata": {"usage": raw.get("usage")}}
        if kind == "run.failed":
            return {"type": "error", "text": str(raw.get("error") or "Hermes run failed."), "metadata": {"source_event": kind}}
        if kind == "run.cancelled":
            return {"type": "cancelled", "text": "Hermes run cancelled.", "metadata": {"source_event": kind}}
        return None

    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("hermes_run_reply_not_supported")

    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("hermes_run_steer_not_supported")

    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        choice = _validated_approval_choice(task, payload)
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.url}/v1/runs/{task['remote_task_id']}/approval",
                json={"choice": choice, "resolve_all": False},
                headers=self._headers(task),
            )
        response.raise_for_status()
        return response.json()

    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.url}/v1/runs/{task['remote_task_id']}/stop",
                json={},
                headers=self._headers(task),
            )
        response.raise_for_status()
        return response.json()

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                public = await client.get(f"{self.url}/health")
                public.raise_for_status()
                capabilities = await client.get(f"{self.url}/v1/capabilities", headers=self._headers())
            capabilities.raise_for_status()
            capabilities_payload = capabilities.json()
            features = (
                capabilities_payload.get("features") or {}
                if isinstance(capabilities_payload, dict)
                else {}
            )
            permission_profile = (
                capabilities_payload.get("permission_profile")
                if isinstance(capabilities_payload, dict)
                else None
            )
            if permission_profile != "read_only_enforced" and features.get("read_only_enforced") is not True:
                return {"state": "incompatible", "reason": "read_only_not_enforced"}
            public_payload = public.json()
            public_payload = public_payload if isinstance(public_payload, dict) else {}
            result = {
                "state": "connected",
                "machine": self.machine,
                "protocol": "hermes-runs",
                "display_name": self.label,
                "installation_capabilities": ["hermes"],
                **_hermes_run_features(features),
            }
            version = str(public_payload.get("version") or "").strip()
            if version:
                result["version"] = version[:80]
            return result
        except Exception as exc:
            return {"machine": self.machine, **_health_failure(exc)}


PC_TOKEN_FILE = Path(os.getenv("ODYSSEUS_AGENT_BRIDGE_TOKEN_FILE", "/etc/odysseus-agent-bridge-token"))
HERMES_TOKEN_FILE = Path(os.getenv("ODYSSEUS_HERMES_TOKEN_FILE", "/etc/odysseus-hermes-token"))
VPS_TOKEN_FILE = Path(os.getenv("ODYSSEUS_VPS_WORKER_TOKEN_FILE", "/etc/odysseus-vps-worker-token"))

_AGENT_ADAPTER_TTL = 5.0
_AGENT_ADAPTER_CACHE: dict[str, Any] = {"registry": {}, "time": 0.0, "loaded": False}


def invalidate_agent_adapter_cache() -> None:
    """Drop the registered-node adapter cache after an endpoint row changes."""
    _AGENT_ADAPTER_CACHE.update({"registry": {}, "time": 0.0, "loaded": False})


def _agent_endpoint_rows() -> list[Any]:
    """Read registered node-agent rows; best-effort so read paths degrade closed."""
    try:
        from core.database import ModelEndpoint, SessionLocal
    except Exception:
        return []
    db = None
    try:
        db = SessionLocal()
        rows = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.endpoint_kind == AGENT_ENDPOINT_KIND)
            .all()
        )
        return [row for row in rows] if isinstance(rows, (list, tuple)) else []
    except Exception:
        return []
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


def agent_endpoint_adapters() -> dict[str, CodexBridgeAdapter]:
    """Build codex-bridge adapters for every enabled registered node endpoint.

    Short-TTL cached because adapters() is called on hot paths; invalidate with
    :func:`invalidate_agent_adapter_cache` after a registration change.
    """
    now = time.monotonic()
    if (
        _AGENT_ADAPTER_CACHE["loaded"]
        and (now - float(_AGENT_ADAPTER_CACHE["time"] or 0.0)) < _AGENT_ADAPTER_TTL
    ):
        return dict(_AGENT_ADAPTER_CACHE["registry"] or {})
    registry: dict[str, CodexBridgeAdapter] = {}
    for row in _agent_endpoint_rows():
        try:
            endpoint_id = str(getattr(row, "id", "") or "").strip()
            base_url = str(getattr(row, "base_url", "") or "").strip().rstrip("/")
            if not endpoint_id or not base_url:
                continue
            worker = agent_worker_id(endpoint_id)
            registry[worker] = CodexBridgeAdapter(
                worker,
                base_url,
                None,
                token=str(getattr(row, "api_key", "") or ""),
                enabled=bool(getattr(row, "is_enabled", False)),
                machine="Registered node",
                label=_display_name(getattr(row, "name", ""), worker),
                workspaces=agent_endpoint_workspaces(row),
            )
        except Exception:
            continue
    _AGENT_ADAPTER_CACHE.update({"registry": registry, "time": now, "loaded": True})
    return dict(registry)


def ensure_legacy_codex_endpoint() -> bool:
    """Register the env-configured legacy Codex bridge as a node endpoint once.

    MAD-934 migration: after this row exists, the ``pc-codex`` worker resolves
    from the endpoint row instead of the fixed environment binding, so the
    existing chat/voice/task behavior reads the binding as data. Idempotent and
    best-effort — it never raises into a caller's read path.
    """
    if not _enabled("ODYSSEUS_PC_CODEX_ENABLED", False):
        return False
    try:
        from core.database import ModelEndpoint, SessionLocal
    except Exception:
        return False
    db = None
    try:
        db = SessionLocal()
        if db.query(ModelEndpoint).filter(ModelEndpoint.id == LEGACY_CODEX_ENDPOINT_ID).first() is not None:
            return False
        base_url = str(
            os.getenv("ODYSSEUS_PC_CODEX_URL", "http://127.0.0.1:8040") or ""
        ).strip().rstrip("/")
        if not base_url:
            return False
        from datetime import datetime

        meta = {
            "protocol": AGENT_BRIDGE_PROTOCOLS[0],
            "workspaces": list(
                configured_worker_workspaces().get(LEGACY_CODEX_ENDPOINT_ID, [])
            ),
        }
        row = ModelEndpoint(
            id=LEGACY_CODEX_ENDPOINT_ID,
            name=_worker_label("ODYSSEUS_PC_CODEX_LABEL", "PC Codex"),
            base_url=base_url,
            api_key=_token(PC_TOKEN_FILE) or None,
            is_enabled=True,
            model_type=AGENT_ENDPOINT_KIND,
            endpoint_kind=AGENT_ENDPOINT_KIND,
            agent_meta=json.dumps(meta),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
        invalidate_agent_adapter_cache()
        return True
    except Exception:
        try:
            if db is not None:
                db.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


def adapters(*, include_external: bool = False) -> dict[str, WorkerAdapter]:
    registry: dict[str, WorkerAdapter] = {
        "pc-codex": CodexBridgeAdapter(
            "pc-codex",
            os.getenv("ODYSSEUS_PC_CODEX_URL", "http://127.0.0.1:8040"),
            PC_TOKEN_FILE,
            enabled=_enabled("ODYSSEUS_PC_CODEX_ENABLED", False),
            machine="Local workstation",
            label=_worker_label("ODYSSEUS_PC_CODEX_LABEL", "PC Codex"),
        ),
        "hermes": HermesRunsAdapter(
            os.getenv("ODYSSEUS_HERMES_URL", "http://127.0.0.1:8642"),
            HERMES_TOKEN_FILE,
            enabled=_enabled("ODYSSEUS_HERMES_ENABLED", False),
            label=_worker_label("ODYSSEUS_HERMES_LABEL", "Hermes"),
        ),
        "vps-codex": CodexBridgeAdapter(
            "vps-codex",
            os.getenv("ODYSSEUS_VPS_CODEX_URL", "http://127.0.0.1:8650"),
            VPS_TOKEN_FILE,
            enabled=_enabled("ODYSSEUS_VPS_CODEX_ENABLED", False),
            machine="Remote server",
            label=_worker_label("ODYSSEUS_VPS_CODEX_LABEL", "VPS Codex"),
        ),
    }
    # Registered node-agent endpoints are the data-driven source of truth and
    # override the fixed env slots (the migrated legacy binding keeps the
    # pc-codex worker id, so existing chat/voice/task bindings still resolve).
    registry.update(agent_endpoint_adapters())
    if include_external:
        from src.external_agent_bridge import external_agent_adapters

        registry.update(external_agent_adapters())
    return registry


def worker_catalog(
    registry: dict[str, WorkerAdapter] | None = None,
) -> dict[str, dict[str, Any]]:
    registry = registry or adapters()
    workspaces = configured_worker_workspaces()
    metadata = {
        "pc-codex": ("codex-bridge", "Local workstation", ["read_only_inspection", "code", "artifacts"]),
        "hermes": ("hermes-runs", "Remote agent", ["remote_agent", "approvals", "session_memory"]),
        "vps-codex": ("codex-bridge", "Remote server", ["read_only_inspection"]),
    }
    result: dict[str, dict[str, Any]] = {}
    for worker, adapter in registry.items():
        adapter_name, machine, capabilities = metadata.get(
            worker,
            (
                getattr(adapter, "adapter_name", "worker"),
                getattr(adapter, "machine", "Configured worker"),
                getattr(adapter, "catalog_capabilities", ["read_only"]),
            ),
        )
        result[worker] = {
            "id": worker,
            "label": getattr(adapter, "label", worker),
            "enabled": bool(adapter.enabled),
            "configured": bool(adapter.enabled),
            "ready": False,
            "adapter": getattr(adapter, "adapter_name", adapter_name),
            "machine": machine,
            "capabilities": capabilities,
            "workspaces": list(
                getattr(adapter, "configured_workspaces", None)
                or workspaces.get(worker, [])
            ),
        }
    return result


def configured_worker(worker: str) -> dict[str, Any]:
    """Resolve one configured worker, loading optional adapters only on demand."""
    worker = str(worker or "").strip()
    registry = adapters()
    if worker not in registry:
        from src.external_agent_bridge import ExternalAgentBridgeError

        try:
            registry = adapters(include_external=True)
        except ExternalAgentBridgeError:
            return {}
    return worker_catalog(registry).get(worker) or {}


async def probe_worker_statuses(
    registry: dict[str, WorkerAdapter],
    catalog: dict[str, dict[str, Any]],
    *,
    owner: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Probe configured adapters and expose one redacted status contract."""
    configured = [
        (worker, adapter)
        for worker, adapter in registry.items()
        if bool(adapter.enabled) and worker in catalog
    ]
    health_rows = await asyncio.gather(
        *(
            adapter.health(owner=owner)
            if owner is not None and getattr(adapter, "adapter_name", "") == "external-agent-sidecar"
            else adapter.health()
            for _, adapter in configured
        ),
        return_exceptions=True,
    )
    result: dict[str, dict[str, Any]] = {}
    for (worker, adapter), health in zip(configured, health_rows):
        health = health if isinstance(health, dict) else {
            "state": "unreachable",
            "reason": str(getattr(health, "code", "connection_failed")),
        }
        state = str(health.get("state") or "unreachable")
        connection = {"state": state}
        for key in ("reason", "protocol"):
            value = str(health.get(key) or "").strip()
            if value:
                connection[key] = value[:120]
        if "protocol_ready" in health:
            connection["protocol_ready"] = health.get("protocol_ready") is True
        ready = state == "connected"
        result[worker] = {
            **catalog[worker],
            "label": _display_name(
                health.get("display_name"),
                catalog[worker].get("label") or worker,
            ),
            "configured": True,
            "ready": ready,
            "enabled": ready,
            "installation_capabilities": installation_capabilities(health),
            "connection": connection,
        }
    return result
