"""The setup wizard replaces the passive checklist with guided, admin-aware steps."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")
WIZARD_JS = (REPO_ROOT / "static" / "js" / "setupWizard.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_wizard_is_wired_to_the_guide_modal_and_guide_button():
    assert "guide-modal" in (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "userBarGuide.hidden = false" in APP_JS
    assert "setupWizardModule.open()" in APP_JS


def test_wizard_auto_opens_only_for_admins_with_incomplete_required_steps():
    assert "export async function maybeAutoOpen(authStatus)" in WIZARD_JS
    assert "if (!authStatus || !authStatus.is_admin) return;" in WIZARD_JS
    assert "_isDismissed()" in WIZARD_JS
    assert "status.identity?.configured && status.model?.usable" in WIZARD_JS


def test_wizard_never_renders_raw_backend_error_codes():
    forbidden = (
        "extension_",
        "marketplace_",
        "provider_not_admitted",
        "update_failed",
    )
    for token in forbidden:
        assert token not in WIZARD_JS
    assert "could not" in WIZARD_JS or "couldn't" in WIZARD_JS


def test_non_admin_welcome_copy_points_at_the_administrator():
    assert "Setup is managed by your administrator." in APP_JS
    assert "window._isAdmin === false" in APP_JS
    assert "setup-wizard-link" in APP_JS


def test_wizard_dismissal_is_persistent_not_session_scoped():
    assert "Storage.get(DISMISS_KEY)" in WIZARD_JS
    assert "Storage.set(DISMISS_KEY, '1')" in WIZARD_JS
    assert "sessionStorage" not in WIZARD_JS


def test_wizard_styles_replaced_the_retired_checklist_classes():
    assert ".setup-wizard" in STYLE_CSS
    assert ".setup-lane" in STYLE_CSS
    assert ".first-run-guide" not in STYLE_CSS
