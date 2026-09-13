import { expect, test } from '@playwright/test';


async function stubApis(page, state) {
  await page.route('**/api/**', route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({
        json: {
          username: 'tester',
          is_admin: state.isAdmin,
          privileges: {},
          agent_identity: {
            source: state.identityConfigured ? 'configured' : 'default',
            display_name: state.identityConfigured ? 'Friday' : 'Assistant',
            status: 'healthy',
          },
        },
      });
    }
    if (path === '/api/setup/status') {
      return route.fulfill({
        json: {
          is_admin: state.isAdmin,
          identity: {
            configured: state.identityConfigured,
            display_name: state.identityConfigured ? 'Friday' : 'Assistant',
            status: 'healthy',
          },
          model: { usable: false, endpoints: 0, models: 0 },
          voice: { ready: false, enabled: true, provider: 'disabled' },
          integrations: { configured: 0, portal_connected: false },
          extensions: { installed: 0, enabled: 0 },
          update: state.isAdmin
            ? { version: '1.0.55', state: 'idle', target_version: null, rollback_available: false }
            : null,
        },
      });
    }
    if (path === '/api/auth/settings' && request.method() === 'POST') {
      state.identityConfigured = true;
      return route.fulfill({ json: { agent_display_name: 'Friday' } });
    }
    if (path === '/api/gallery/discovery') {
      return route.fulfill({ json: { connected: 0, sources: [] } });
    }
    if (path === '/api/models' || path === '/api/model-endpoints' || path === '/api/sessions') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}


test('first-run admin is guided to name the assistant without opening settings', async ({ page }) => {
  const state = { isAdmin: true, identityConfigured: false };
  await stubApis(page, state);
  await page.goto('/static/index.html');

  const modal = page.locator('#guide-modal');
  await expect(modal).not.toHaveClass(/hidden/);
  await expect(modal).toContainText('Set up Pandamonium');
  await expect(modal.getByRole('button', { name: 'Name it' })).toBeFocused();
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Assistant name' }))
    .toContainText('Required');

  await modal.getByRole('button', { name: 'Name it' }).click();
  await expect(modal).toContainText('What should we call your assistant?');
  const input = modal.locator('.setup-wizard-field input');
  await input.fill('Friday');
  await modal.getByRole('button', { name: 'Save name' }).click();

  await expect(modal.locator('.setup-wizard-notice')).toContainText('Saved — your assistant is now called Friday.');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Assistant name' }))
    .toContainText('Ready — Friday');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Model engine' }))
    .toContainText('Required');
});


test('non-admin gets a status-only setup view with no dead-end actions', async ({ page }) => {
  const state = { isAdmin: false, identityConfigured: false };
  await stubApis(page, state);
  await page.goto('/static/index.html');

  const modal = page.locator('#guide-modal');
  await expect(modal).toHaveClass(/hidden/);

  const guideButton = page.locator('#user-bar-guide');
  await expect(guideButton).toBeVisible();
  await guideButton.click();

  await expect(modal).not.toHaveClass(/hidden/);
  await expect(modal).toContainText('Setup status');
  await expect(modal).toContainText('Managed by your administrator');
  await expect(modal.locator('.setup-lane-action')).toHaveCount(0);
  await expect(modal).not.toContainText('Updates');
});
