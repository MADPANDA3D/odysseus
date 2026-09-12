// MAD-917: the agent working-plan panel above the composer — live update,
// progress count, nesting, collapse persistence, and per-session restore.
import { expect, test } from '@playwright/test';

async function installMockRoutes(page) {
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/sessions' || url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

test('agent plan panel renders, collapses, and restores per session', async ({ page }) => {
  await installMockRoutes(page);
  await page.goto('/static/index.html');
  await expect(page.locator('#agent-plan-panel')).toBeHidden();

  await page.evaluate(() => window.agentPlanModule.update(
    '- [x] Verify Portal tools\n- [ ] Read live state\n  - [ ] Validate graph',
  ));
  const panel = page.locator('#agent-plan-panel');
  await expect(panel).toBeVisible();
  await expect(page.locator('#agent-plan-count')).toHaveText('1 of 3 todos completed');
  await expect(page.locator('.agent-plan-item')).toHaveCount(3);
  await expect(page.locator('.agent-plan-item').first()).toHaveClass(/is-done/);
  await expect(page.locator('.agent-plan-item').nth(1)).toHaveClass(/is-active/);
  const nestedDepth = await page.locator('.agent-plan-item').nth(2).evaluate(el => getComputedStyle(el).getPropertyValue('--plan-depth').trim());
  expect(nestedDepth).toBe('1');

  // Collapse persists across a reload and the plan is restored from storage.
  await page.locator('#agent-plan-toggle').click();
  await expect(panel).toHaveClass(/collapsed/);
  expect(await page.evaluate(() => localStorage.getItem('odysseus-agent-plan-collapsed'))).toBe('1');
  await page.reload();
  await expect(page.locator('#agent-plan-panel')).toHaveClass(/collapsed/);
  await expect(page.locator('#agent-plan-count')).toHaveText('1 of 3 todos completed');

  // A plan stored for another session never leaks into the active one.
  await page.evaluate(() => window.agentPlanModule.update('- [ ] Other session step', 'some-other-session'));
  await expect(page.locator('.agent-plan-item')).toHaveCount(3);
  expect(await page.evaluate(() => window.agentPlanModule.parsePlan('- [x] a\n- [ ] b').length)).toBe(2);
});
