// MAD-902/MAD-920: Pandamonium agent-workstation projects are listed inside
// the Chats section, and the hover + starts a session bound to that project.
import { expect, test } from '@playwright/test';

test('Projects group inside Chats lists real projects and starts a bound session', async ({ page }) => {
  let posted = null;
  const projects = [
    { id: 'p1', name: 'Rocket Lab', path: '/work/rocket', resolved_path: '/work/rocket', available: true, reason: '' },
    { id: 'p2', name: 'Ghost', path: '/work/gone', resolved_path: '/work/gone', available: false, reason: 'folder no longer exists' },
  ];
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/projects' && method === 'GET') {
      return route.fulfill({ json: { projects, root: '/data/projects' } });
    }
    if (url.pathname === '/api/projects' && method === 'POST') {
      posted = JSON.parse(route.request().postData() || '{}');
      const created = {
        id: 'p3', name: posted.name, path: `/data/projects/${posted.name}`,
        resolved_path: `/data/projects/${posted.name}`, available: true, reason: '',
      };
      return route.fulfill({ json: { project: created, projects: [...projects, created] } });
    }
    if (url.pathname === '/api/sessions') return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');

  const projectsLabel = page.locator('#session-list .sidebar-nav-label-row', { hasText: 'Projects' });
  await expect(projectsLabel).toBeVisible();
  await expect(page.locator('#session-list .project-folder')).toHaveCount(2);
  await expect(page.locator('#session-list .project-folder .folder-name').first()).toContainText('Rocket Lab');
  await expect(page.locator('#session-list .project-folder').nth(1)).toHaveClass(/project-unavailable/);

  // The hover + binds the project workspace and starts a new session there.
  const folder = page.locator('#session-list .project-folder').first();
  await folder.hover();
  await folder.locator('.project-session-btn').click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('odysseus-workspace'))).toBe('/work/rocket');

  // Unavailable projects never replace the bound workspace.
  const ghost = page.locator('#session-list .project-folder').nth(1);
  await ghost.hover();
  await expect(ghost.locator('.project-session-btn')).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem('odysseus-workspace'))).toBe('/work/rocket');

  // The Projects + opens the add menu with both paths.
  await page.locator('#projects-add-btn').click();
  await expect(page.locator('#projects-add-menu')).toBeVisible();
  await expect(page.locator('#project-create-option')).toContainText('New project');
  await expect(page.locator('#project-import-option')).toContainText('Add existing folder');

  // New project posts the name and refreshes the list.
  await page.locator('#project-create-option').click();
  await page.locator('#styled-prompt-input').fill('Demo');
  await page.locator('#styled-prompt-ok').click();
  await expect.poll(() => posted && posted.name).toBe('Demo');
  await expect(page.locator('#session-list .project-folder')).toHaveCount(3);
});

test('Projects group shows only the header and + when no projects exist', async ({ page }) => {
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/projects') return route.fulfill({ json: { projects: [], root: '/data/projects' } });
    if (url.pathname === '/api/sessions') return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');

  await expect(page.locator('#session-list .sidebar-nav-label-row', { hasText: 'Projects' })).toBeVisible();
  await expect(page.locator('#projects-add-btn')).toBeVisible();
  await expect(page.locator('#session-list .project-folder')).toHaveCount(0);
  await expect(page.locator('#session-list')).not.toContainText('No projects');
});
