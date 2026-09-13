"""MAD-934: node agent endpoints — registration, pairing, migration, availability.

These tests exercise the real ModelEndpoint table in a temp SQLite database and
the real route helpers. The bridge itself is replaced with a fake HTTP
transport, so nothing here dials a network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

from tests.helpers.sqlite_db import make_temp_sqlite
from tests.helpers.import_state import preserve_import_state

with preserve_import_state("routes.model_routes", "src.agent_worker_adapters"):
    import core.database as database
    import routes.model_routes as model_routes
    import src.agent_worker_adapters as adapters_module


BRIDGE_PROTOCOL = "pandamonium.codex-bridge.v2"
NODE_TOKEN = "node-pairing-token-123"


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _admin_request(user: str = "admin"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _health_payload(**overrides):
    payload = {
        "ok": True,
        "app_server": True,
        "protocol_version": BRIDGE_PROTOCOL,
        "features": {"project_catalog": True, "task_control": True},
        "installation": {"display_name": "Workstation Bridge"},
    }
    payload.update(overrides)
    return payload


def _fake_get(payload, status_code: int = 200):
    def _get(url, **kwargs):
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = payload
        if status_code >= 400:
            response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "boom", request=httpx.Request("GET", url), response=response
            )
        else:
            response.raise_for_status.return_value = None
        return response

    return _get


@pytest.fixture
def agent_db(monkeypatch):
    SessionLocal, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(model_routes, "SessionLocal", SessionLocal)
    adapters_module.invalidate_agent_adapter_cache()
    yield SessionLocal
    engine.dispose()
    tmpfile.close()


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setattr(model_routes, "require_admin", lambda request: None)
    return model_routes.setup_model_routes(MagicMock())


def _insert_agent_row(SessionLocal, endpoint_id="agent01", **overrides):
    from datetime import datetime

    values = {
        "id": endpoint_id,
        "name": "Node One",
        "base_url": "http://127.0.0.1:8040",
        "api_key": NODE_TOKEN,
        "is_enabled": True,
        "model_type": "agent",
        "endpoint_kind": "agent",
        "agent_meta": json.dumps({"protocol": "codex-bridge", "workspaces": ["home-lab"]}),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    values.update(overrides)
    with SessionLocal() as session:
        session.add(database.ModelEndpoint(**values))
        session.commit()


# ── pairing ──────────────────────────────────────────────────────────────


def test_pair_agent_bridge_reports_connected_without_leaking_token(monkeypatch):
    monkeypatch.setattr(model_routes.httpx, "get", _fake_get(_health_payload()))

    result = model_routes._probe_agent_bridge("http://127.0.0.1:8040", NODE_TOKEN)

    assert result["ok"] is True
    assert result["state"] == "connected"
    assert result["protocol"] == "codex-bridge"
    assert result["protocol_version"] == BRIDGE_PROTOCOL
    assert result["node_label"] == "Workstation Bridge"
    assert NODE_TOKEN not in json.dumps(result)


def test_pair_agent_bridge_auth_failure_fails_closed_with_human_copy(monkeypatch):
    monkeypatch.setattr(
        model_routes.httpx, "get", _fake_get({"ok": False}, status_code=401)
    )

    result = model_routes._probe_agent_bridge("http://127.0.0.1:8040", "wrong-token")

    assert result["ok"] is False
    assert result["state"] == "auth_required"
    assert "wrong-token" not in json.dumps(result)
    assert "code" in result["message"].lower() or "token" in result["message"].lower()


def test_pair_agent_bridge_unreachable_fails_closed(monkeypatch):
    def _boom(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(model_routes.httpx, "get", _boom)

    result = model_routes._probe_agent_bridge("http://127.0.0.1:9", NODE_TOKEN)

    assert result["ok"] is False
    assert result["state"] == "unreachable"
    assert result["reason"] == "connection_failed"
    assert NODE_TOKEN not in json.dumps(result)


def test_pair_agent_bridge_incompatible_protocol_fails_closed(monkeypatch):
    monkeypatch.setattr(
        model_routes.httpx,
        "get",
        _fake_get(_health_payload(protocol_version="pandamonium.codex-bridge.v1")),
    )

    result = model_routes._probe_agent_bridge("http://127.0.0.1:8040", NODE_TOKEN)

    assert result["ok"] is False
    assert result["state"] == "incompatible"
    assert result["reason"] == "bridge_update_required"


# ── registration ─────────────────────────────────────────────────────────


def test_register_agent_endpoint_requires_live_pairing(agent_db, router, monkeypatch):
    def _boom(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(model_routes.httpx, "get", _boom)
    create = _route(router, "/api/model-endpoints", "POST")

    with pytest.raises(HTTPException) as excinfo:
        create(
            _admin_request(),
            name="Node One",
            base_url="http://127.0.0.1:8040",
            api_key=NODE_TOKEN,
            endpoint_kind="agent",
            bridge_protocol="codex-bridge",
            shared="true",
            skip_probe="true",
        )

    assert excinfo.value.status_code == 400
    assert "pair" in excinfo.value.detail.lower() or "reach" in excinfo.value.detail.lower()
    with agent_db() as session:
        assert session.query(database.ModelEndpoint).count() == 0


def test_register_agent_endpoint_requires_pairing_token(agent_db, router):
    create = _route(router, "/api/model-endpoints", "POST")

    with pytest.raises(HTTPException) as excinfo:
        create(
            _admin_request(),
            name="Node One",
            base_url="http://127.0.0.1:8040",
            api_key="",
            endpoint_kind="agent",
            bridge_protocol="codex-bridge",
            shared="true",
        )

    assert excinfo.value.status_code == 400
    with agent_db() as session:
        assert session.query(database.ModelEndpoint).count() == 0


def test_register_agent_endpoint_persists_redacted_agent_row(agent_db, router, monkeypatch):
    monkeypatch.setattr(model_routes.httpx, "get", _fake_get(_health_payload()))
    create = _route(router, "/api/model-endpoints", "POST")

    result = create(
        _admin_request(),
        name="",
        base_url="http://127.0.0.1:8040",
        api_key=NODE_TOKEN,
        endpoint_kind="agent",
            bridge_protocol="codex-bridge",
            shared="true",
        workspaces="home-lab, project-linux",
    )

    assert result["endpoint_kind"] == "agent"
    assert result["model_type"] == "agent"
    assert result["status"] == "connected"
    assert result["has_key"] is True
    assert result["models"] == []
    assert result["name"] == "Workstation Bridge"
    serialized = json.dumps(result)
    assert NODE_TOKEN not in serialized
    assert "Bearer" not in serialized

    with agent_db() as session:
        row = session.query(database.ModelEndpoint).one()
    assert row.endpoint_kind == "agent"
    assert row.model_type == "agent"
    assert row.api_key == NODE_TOKEN
    meta = json.loads(row.agent_meta)
    assert meta["protocol"] == "codex-bridge"
    assert meta["workspaces"] == ["home-lab", "project-linux"]


def test_register_agent_endpoint_rejects_unknown_protocol(agent_db, router):
    create = _route(router, "/api/model-endpoints", "POST")

    with pytest.raises(HTTPException) as excinfo:
        create(
            _admin_request(),
            base_url="http://127.0.0.1:8040",
            api_key=NODE_TOKEN,
            endpoint_kind="agent",
            bridge_protocol="cloud-oauth-reverse",
        )

    assert excinfo.value.status_code == 400
    assert "protocol" in excinfo.value.detail.lower()


def test_register_agent_endpoint_does_not_become_chat_default(agent_db, router, monkeypatch):
    monkeypatch.setattr(model_routes.httpx, "get", _fake_get(_health_payload()))
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    saved = {}
    monkeypatch.setattr(model_routes, "_save_settings", lambda value: saved.update(value))
    create = _route(router, "/api/model-endpoints", "POST")

    create(
        _admin_request(),
        base_url="http://127.0.0.1:8040",
        api_key=NODE_TOKEN,
        endpoint_kind="agent",
            bridge_protocol="codex-bridge",
            shared="true",
    )

    assert "default_endpoint_id" not in saved


# ── availability ─────────────────────────────────────────────────────────


def test_list_agent_endpoint_exposes_protocol_and_availability(agent_db, router, monkeypatch):
    _insert_agent_row(agent_db)
    monkeypatch.setattr(model_routes.httpx, "get", _fake_get(_health_payload()))
    model_routes._invalidate_agent_health_cache()
    list_endpoints = _route(router, "/api/model-endpoints", "GET")

    rows = list_endpoints(_admin_request())

    assert len(rows) == 1
    row = rows[0]
    assert row["endpoint_kind"] == "agent"
    assert row["model_type"] == "agent"
    assert row["category"] == "agent"
    assert row["status"] == "connected"
    assert row["online"] is True
    assert row["protocol"] == "codex-bridge"
    assert row["connection"]["protocol_ready"] is True
    assert row["workspaces"] == ["home-lab"]
    assert row["models"] == []
    assert NODE_TOKEN not in json.dumps(row)


def test_list_agent_endpoint_reports_unreachable_without_secrets(agent_db, router, monkeypatch):
    _insert_agent_row(agent_db)

    def _boom(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(model_routes.httpx, "get", _boom)
    model_routes._invalidate_agent_health_cache()
    list_endpoints = _route(router, "/api/model-endpoints", "GET")

    rows = list_endpoints(_admin_request())

    assert rows[0]["status"] == "unreachable"
    assert rows[0]["online"] is False
    assert rows[0]["connection"]["reason"] == "connection_failed"
    assert NODE_TOKEN not in json.dumps(rows[0])


def test_disabled_agent_endpoint_is_reported_disabled(agent_db, router, monkeypatch):
    _insert_agent_row(agent_db, is_enabled=False)
    monkeypatch.setattr(model_routes.httpx, "get", _fake_get(_health_payload()))
    model_routes._invalidate_agent_health_cache()
    list_endpoints = _route(router, "/api/model-endpoints", "GET")

    rows = list_endpoints(_admin_request())

    assert rows[0]["is_enabled"] is False
    assert rows[0]["status"] == "disabled"
    assert rows[0]["online"] is False


# ── migration ────────────────────────────────────────────────────────────


def test_legacy_codex_binding_migrates_to_registered_agent_endpoint(
    agent_db, monkeypatch, tmp_path
):
    token_file = tmp_path / "bridge-token"
    token_file.write_text("legacy-token", encoding="utf-8")
    monkeypatch.setenv("ODYSSEUS_PC_CODEX_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_PC_CODEX_URL", "http://127.0.0.1:8040")
    monkeypatch.setenv("ODYSSEUS_PC_CODEX_LABEL", "Desk Bridge")
    monkeypatch.setenv(
        "ODYSSEUS_WORKER_WORKSPACES_JSON", '{"pc-codex": ["home-lab", "project-linux"]}'
    )
    monkeypatch.setattr(adapters_module, "PC_TOKEN_FILE", token_file)

    assert adapters_module.ensure_legacy_codex_endpoint() is True
    assert adapters_module.ensure_legacy_codex_endpoint() is False

    with agent_db() as session:
        row = session.query(database.ModelEndpoint).one()
    assert row.id == "pc-codex"
    assert row.endpoint_kind == "agent"
    assert row.model_type == "agent"
    assert row.base_url == "http://127.0.0.1:8040"
    assert row.api_key == "legacy-token"
    assert row.is_enabled is True
    assert row.name == "Desk Bridge"
    meta = json.loads(row.agent_meta)
    assert meta["workspaces"] == ["home-lab", "project-linux"]

    # The binding is data: after migration, the adapter registry resolves the
    # pc-codex worker from the row, even if the env URL changes afterwards.
    monkeypatch.setenv("ODYSSEUS_PC_CODEX_URL", "http://127.0.0.1:9999")
    adapters_module.invalidate_agent_adapter_cache()
    registry = adapters_module.adapters()
    assert registry["pc-codex"].url == "http://127.0.0.1:8040"
    assert registry["pc-codex"].label == "Desk Bridge"


def test_legacy_migration_skips_when_not_configured(agent_db, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_PC_CODEX_ENABLED", raising=False)
    monkeypatch.delenv("ODYSSEUS_PC_CODEX_URL", raising=False)

    assert adapters_module.ensure_legacy_codex_endpoint() is False
    with agent_db() as session:
        assert session.query(database.ModelEndpoint).count() == 0


def test_registered_agent_endpoints_join_worker_catalog(agent_db, monkeypatch):
    _insert_agent_row(agent_db)
    adapters_module.invalidate_agent_adapter_cache()

    registry = adapters_module.adapters()
    assert "agent-agent01" in registry
    adapter = registry["agent-agent01"]
    assert adapter.url == "http://127.0.0.1:8040"
    assert adapter.label == "Node One"
    assert adapter.configured_workspaces == ["home-lab"]

    catalog = adapters_module.worker_catalog(registry)
    assert catalog["agent-agent01"]["label"] == "Node One"
    assert catalog["agent-agent01"]["workspaces"] == ["home-lab"]
    assert catalog["agent-agent01"]["enabled"] is True


def test_disabled_agent_endpoint_is_not_reported_ready(agent_db, monkeypatch):
    _insert_agent_row(agent_db, is_enabled=False)
    adapters_module.invalidate_agent_adapter_cache()

    catalog = adapters_module.worker_catalog()

    assert catalog["agent-agent01"]["enabled"] is False
