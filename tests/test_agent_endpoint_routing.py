"""MAD-934: registered node agents route through the existing worker lanes."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.helpers.import_state import preserve_import_state
from tests.helpers.sqlite_db import make_temp_sqlite

with preserve_import_state(
    "routes.agent_task_routes", "routes.chat_routes", "routes.voice_routes", "src.agent_worker_adapters"
):
    import core.database as database
    import routes.agent_task_routes as agent_task_routes
    import routes.chat_routes as chat_routes
    import routes.voice_routes as voice_routes
    import src.agent_worker_adapters as adapters_module
    import src.jarvis_agent as jarvis_agent


AGENT_ENDPOINT_ID = "a1b2c3d4"
AGENT_WORKER_ID = f"agent-{AGENT_ENDPOINT_ID}"


class _Manager:
    def get_session(self, session_id):
        if session_id != "session-1":
            raise KeyError(session_id)
        return SimpleNamespace(id=session_id, owner="leo", agent_target=AGENT_WORKER_ID)


@pytest.fixture
def agent_db(monkeypatch):
    SessionLocal, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    adapters_module.invalidate_agent_adapter_cache()
    yield SessionLocal
    engine.dispose()
    tmpfile.close()


def _insert_agent_row(SessionLocal):
    with SessionLocal() as session:
        session.add(database.ModelEndpoint(
            id=AGENT_ENDPOINT_ID,
            name="Workstation Node",
            base_url="http://127.0.0.1:8040",
            api_key="paired-token",
            is_enabled=True,
            model_type="agent",
            endpoint_kind="agent",
            agent_meta=json.dumps({"protocol": "codex-bridge", "workspaces": ["home-lab"]}),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
        session.commit()


def _route(router, name: str):
    return next(route.endpoint for route in router.routes if route.name == name)


@pytest.mark.asyncio
async def test_registered_agent_worker_is_accepted_by_task_control(agent_db, monkeypatch):
    _insert_agent_row(agent_db)
    adapters_module.invalidate_agent_adapter_cache()
    started = []

    class Authority:
        def decide(self, call, **_kwargs):
            return {
                "decision_id": "decision-1",
                "approval_decision_id": None,
                "decision": "allow",
                "permission_mode": "read_only",
                "action_effect": "read",
                "policy_basis": "authenticated_explicit_request",
            }

    async def start_task(**values):
        started.append(values)
        return {
            "task_id": "task-agent",
            "session_id": values["session_id"],
            "worker": values["worker"],
            "workspace": values["workspace"],
            "permission_mode": values["permission_mode"],
            "status": "queued",
            "owner": values["owner"],
            "artifacts": [],
        }

    monkeypatch.setattr(agent_task_routes, "authority_store", Authority())
    monkeypatch.setattr(agent_task_routes, "start_task", start_task)
    monkeypatch.setattr(agent_task_routes, "record_operational_event", lambda **values: {"event_id": "audit"})
    router = agent_task_routes.setup_agent_task_routes(_Manager())
    create = _route(router, "create")

    task = await create(
        agent_task_routes.TaskCreate(
            worker=AGENT_WORKER_ID,
            session_id="session-1",
            workspace="home-lab",
            prompt="Inspect the fixture.",
        ),
        SimpleNamespace(),
        owner="leo",
    )

    assert task["worker"] == AGENT_WORKER_ID
    assert started[0]["worker"] == AGENT_WORKER_ID


@pytest.mark.asyncio
async def test_unknown_worker_is_still_rejected(agent_db, monkeypatch):
    router = agent_task_routes.setup_agent_task_routes(_Manager())
    create = _route(router, "create")
    monkeypatch.setattr(agent_task_routes, "record_operational_event", lambda **values: {"event_id": "audit"})

    with pytest.raises(HTTPException) as excinfo:
        await create(
            agent_task_routes.TaskCreate(
                worker="agent-not-registered",
                session_id="session-1",
                workspace="home-lab",
                prompt="Inspect the fixture.",
            ),
            SimpleNamespace(),
            owner="leo",
        )

    assert excinfo.value.status_code == 400
    assert "unknown_worker" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_external_agent_workers_endpoint_excludes_registered_nodes(monkeypatch):
    async def worker_statuses(**kwargs):
        return {
            "pc-codex": {"adapter": "codex-bridge", "configured": True},
            AGENT_WORKER_ID: {"adapter": "codex-bridge", "configured": True},
            "sidecar-1": {"adapter": "external-agent-sidecar", "configured": True},
        }

    monkeypatch.setattr(agent_task_routes, "worker_statuses", worker_statuses)
    router = agent_task_routes.setup_agent_task_routes(_Manager())
    endpoint = _route(router, "external_agent_workers")

    result = await endpoint(owner="leo")

    assert set(result) == {"sidecar-1"}


def test_direct_worker_route_classifies_registered_codex_bridge(monkeypatch):
    def adapter_details(worker):
        if worker in {AGENT_WORKER_ID, "pc-codex"}:
            return {"adapter": "codex-bridge", "configured": True}
        return {}

    monkeypatch.setattr(chat_routes, "_worker_adapter_details", adapter_details)

    assert chat_routes._direct_worker_route("hermes") == "hermes"
    assert chat_routes._direct_worker_route(AGENT_WORKER_ID) == "codex"
    assert chat_routes._direct_worker_route("pc-codex") == "codex"
    assert chat_routes._direct_worker_route("agent-missing") == ""


def test_direct_codex_turn_uses_selected_worker(monkeypatch):
    captured = {}

    def fake_find_active_task(session_id, worker, workspace=None, owner=None):
        captured["find_worker"] = worker
        return None

    def fake_get_worker_binding(owner, session_id, worker, workspace):
        captured["binding_worker"] = worker
        return {}

    async def fake_start_task(worker, session_id, workspace, prompt, **kwargs):
        captured["start_worker"] = worker
        return {"task_id": "task-1", "status": "queued", "workspace": workspace}

    monkeypatch.setattr(jarvis_agent, "find_active_task", fake_find_active_task)
    monkeypatch.setattr(jarvis_agent, "get_worker_binding", fake_get_worker_binding)
    monkeypatch.setattr(jarvis_agent, "start_task", fake_start_task)

    task, action = asyncio.run(jarvis_agent.direct_codex_turn(
        "session-1",
        "Inspect the fixture.",
        owner="leo",
        workspace="home-lab",
        presenter="Workstation Node",
        worker=AGENT_WORKER_ID,
    ))

    assert captured == {
        "find_worker": AGENT_WORKER_ID,
        "binding_worker": AGENT_WORKER_ID,
        "start_worker": AGENT_WORKER_ID,
    }
    assert task["task_id"] == "task-1"
    assert action == "started"


def test_codex_bridge_binding_is_conversation_scoped_for_registered_agent(agent_db):
    _insert_agent_row(agent_db)
    adapters_module.invalidate_agent_adapter_cache()

    key = jarvis_agent._binding_key("leo", "session-1", AGENT_WORKER_ID, "home-lab")
    other_workspace = jarvis_agent._binding_key("leo", "session-1", AGENT_WORKER_ID, "other")

    assert key == other_workspace
    assert f":{AGENT_WORKER_ID}:conversation" in key


def test_registered_agent_is_a_picker_target_with_honest_availability():
    from src.selector_catalog import build_selector_catalog

    statuses = {
        AGENT_WORKER_ID: {
            "id": AGENT_WORKER_ID,
            "label": "Workstation Node",
            "configured": True,
            "enabled": True,
            "ready": True,
            "adapter": "codex-bridge",
            "machine": "Registered node",
            "capabilities": ["read_only"],
            "workspaces": ["home-lab"],
            "installation_capabilities": ["codex"],
            "connection": {"state": "connected", "protocol_ready": True},
        }
    }

    catalog = build_selector_catalog({"items": []}, statuses)
    target = next(s for s in catalog["selections"] if s["target"] == AGENT_WORKER_ID)
    assert target["kind"] == "worker"
    assert target["selectable"] is True
    assert target["runtime"] == "Codex"
    assert target["location"] == "Registered node"

    statuses[AGENT_WORKER_ID] = {
        **statuses[AGENT_WORKER_ID],
        "enabled": False,
        "ready": False,
        "connection": {"state": "unreachable", "reason": "connection_failed"},
    }
    catalog = build_selector_catalog({"items": []}, statuses)
    target = next(s for s in catalog["selections"] if s["target"] == AGENT_WORKER_ID)
    assert target["selectable"] is False
    assert target["reason"] == "connection_failed"


def test_voice_targets_resolve_registered_agents_from_live_catalog(agent_db):
    _insert_agent_row(agent_db)
    adapters_module.invalidate_agent_adapter_cache()

    assert AGENT_WORKER_ID in voice_routes.worker_catalog()
    assert "home-lab" in voice_routes._current_voice_workspaces()
    assert voice_routes._voice_worker_label(AGENT_WORKER_ID) == "Workstation Node"
    assert voice_routes._voice_origin_target({"origin_target": AGENT_WORKER_ID}) == AGENT_WORKER_ID
    assert voice_routes._voice_origin_target({"origin_target": "jarvis"}) == "jarvis"
