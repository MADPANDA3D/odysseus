"""Setup status is live, derived, admin-aware, and never leaks the constitution."""

import asyncio
from types import SimpleNamespace

import routes.setup_routes as setup_routes


class _AuthManager:
    is_configured = True

    def __init__(self, admin: bool = True):
        self._admin = admin

    def is_admin(self, _username: str) -> bool:
        return self._admin


def _route(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


def _request(admin: bool = True, user: str = "operator"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=_AuthManager(admin=admin))),
    )


def test_setup_status_composes_existing_owners_for_admins(monkeypatch):
    monkeypatch.setattr(setup_routes, "_project_identity", lambda: {
        "configured": True,
        "display_name": "Atlas",
        "status": "healthy",
    })
    monkeypatch.setattr(setup_routes, "_project_model", lambda user, is_admin: {
        "usable": True,
        "endpoints": 2,
        "models": 5,
    })
    monkeypatch.setattr(setup_routes, "_project_voice", lambda: {
        "ready": False,
        "enabled": True,
        "provider": "disabled",
    })
    monkeypatch.setattr(setup_routes, "_project_integrations", lambda: {
        "configured": 1,
        "portal_connected": True,
    })
    monkeypatch.setattr(setup_routes, "_project_extensions", lambda: {
        "installed": 3,
        "enabled": 2,
    })
    monkeypatch.setattr(setup_routes, "_project_update", lambda is_admin: {
        "version": "1.0.55",
        "state": "idle",
        "target_version": None,
        "rollback_available": False,
    })

    endpoint = _route(setup_routes.setup_setup_routes(), "/api/setup/status")
    result = asyncio.run(endpoint(_request(admin=True)))

    assert result["is_admin"] is True
    assert result["identity"]["display_name"] == "Atlas"
    assert result["model"] == {"usable": True, "endpoints": 2, "models": 5}
    assert result["integrations"] == {"configured": 1, "portal_connected": True}
    assert result["extensions"] == {"installed": 3, "enabled": 2}
    assert result["update"]["version"] == "1.0.55"


def test_setup_status_hides_update_lane_from_non_admins(monkeypatch):
    monkeypatch.setattr(setup_routes, "_project_identity", lambda: {
        "configured": False,
        "display_name": "Assistant",
        "status": "healthy",
    })
    monkeypatch.setattr(setup_routes, "_project_model", lambda user, is_admin: {
        "usable": False,
        "endpoints": 0,
        "models": 0,
    })
    monkeypatch.setattr(setup_routes, "_project_voice", lambda: {
        "ready": False,
        "enabled": True,
        "provider": "disabled",
    })
    monkeypatch.setattr(setup_routes, "_project_integrations", lambda: {
        "configured": 0,
        "portal_connected": False,
    })
    monkeypatch.setattr(setup_routes, "_project_extensions", lambda: {
        "installed": 0,
        "enabled": 0,
    })

    endpoint = _route(setup_routes.setup_setup_routes(), "/api/setup/status")
    result = asyncio.run(endpoint(_request(admin=False)))

    assert result["is_admin"] is False
    assert result["update"] is None


def test_project_update_is_none_for_non_admins():
    assert setup_routes._project_update(False) is None


def test_project_update_projects_local_updater_state(monkeypatch):
    import src.release_updater as release_updater

    monkeypatch.setattr(release_updater, "public_update_state", lambda: {
        "status": "idle",
        "target_version": "1.0.56",
        "rollback_available": True,
    })
    result = setup_routes._project_update(True)
    assert result == {
        "version": setup_routes.APP_VERSION,
        "state": "idle",
        "target_version": "1.0.56",
        "rollback_available": True,
    }


def test_project_identity_never_includes_constitution(monkeypatch):
    import src.agent_identity as agent_identity

    monkeypatch.setattr(agent_identity, "agent_identity_status", lambda: {
        "agent_id": "atlas",
        "display_name": "Atlas",
        "constitution_version": "2026.1",
        "constitution": "private body",
        "status": "healthy",
        "source": "configured",
        "fallback_reasons": [],
    })
    result = setup_routes._project_identity()
    assert result == {
        "configured": True,
        "display_name": "Atlas",
        "status": "healthy",
    }
    assert "constitution" not in result


def test_visible_model_ids_drops_hidden_models():
    visible = setup_routes._visible_model_ids(
        '["alpha", "beta", "gamma"]',
        '["beta", 7]',
    )
    assert visible == {"alpha", "gamma"}


def test_visible_model_ids_tolerates_invalid_json():
    assert setup_routes._visible_model_ids("not-json", None) == set()
    assert setup_routes._visible_model_ids(None, "not-json") == set()


def test_is_chat_capable_accepts_llm_and_legacy_rows():
    assert setup_routes._is_chat_capable(SimpleNamespace(model_type="llm"))
    assert setup_routes._is_chat_capable(SimpleNamespace(model_type=None))
    assert setup_routes._is_chat_capable(SimpleNamespace(model_type=""))


def test_is_chat_capable_rejects_non_chat_endpoints():
    for model_type in ("image", "stt", "tts"):
        assert not setup_routes._is_chat_capable(SimpleNamespace(model_type=model_type))
