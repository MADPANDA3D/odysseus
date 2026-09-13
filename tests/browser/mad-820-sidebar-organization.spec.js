import { expect, test } from '@playwright/test';

const PROJECT_ORDER_PREF_PATH = '/api/prefs/sidebar-project-order';

function sessionFixture(id, name, { projectId = null, pinned = false, minutes = 0 } = {}) {
  const timestamp = new Date(Date.now() - minutes * 60_000).toISOString();
  return {
    id,
    name,
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 2,
    archived: false,
    is_important: pinned,
    agent_target: 'jarvis',
    project_id: projectId,
    created_at: timestamp,
    updated_at: timestamp,
    last_message_at: timestamp,
  };
}

function projectFixture(id, name, available = true) {
  return {
    id,
    name,
    path: `/work/${id}`,
    resolved_path: `/work/${id}`,
    available,
    reason: available ? '' : 'folder no longer exists',
  };
}

async function mockShell(page, state) {
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === '/api/projects' && request.method() === 'GET') {
      return route.fulfill({ json: { projects: state.projects || [], root: '/data/projects' } });
    }
    if (url.pathname === '/api/selector-catalog' && state.catalog) return route.fulfill({ json: state.catalog });
    if (url.pathname === PROJECT_ORDER_PREF_PATH) {
      if (request.method() === 'PUT') {
        const body = request.postDataJSON();
        state.puts.push(body);
        if (state.prefStatus >= 400) return route.fulfill({ status: state.prefStatus, json: {} });
        state.remoteOrder = body.value;
        return route.fulfill({ json: { key: 'sidebar-project-order', value: state.remoteOrder } });
      }
      if (state.prefStatus >= 400) return route.fulfill({ status: state.prefStatus, json: {} });
      return route.fulfill({
        json: { key: 'sidebar-project-order', value: state.remoteOrder },
      });
    }
    if (url.pathname.startsWith('/api/prefs/')) {
      if (url.pathname === '/api/prefs/sidebar-session-order') {
        if (request.method() === 'PUT') state.sessionOrder = request.postDataJSON().value;
        return route.fulfill({ json: { value: state.sessionOrder ?? null } });
      }
      return route.fulfill({ json: { key: url.pathname.split('/').pop(), value: null } });
    }
    if (url.pathname === '/api/sessions') return route.fulfill({ json: state.sessions });
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: false, privileges: {} } });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    if (url.pathname === '/api/default-chat') return route.fulfill({ json: {} });
    return route.fulfill({ json: {} });
  });
}

function projectOrder(page) {
  return page.locator('#session-list > .session-folder[data-project-id]').evaluateAll(
    folders => folders.map(folder => folder.dataset.projectId),
  );
}

async function dragBefore(page, sourceId, targetId) {
  await page.evaluate(({ sourceId, targetId }) => {
    const source = document.querySelector(`.list-item[data-session-id="${sourceId}"]`);
    const target = document.querySelector(`.list-item[data-session-id="${targetId}"]`);
    const handle = source.querySelector('.item-drag-handle');
    const from = handle.getBoundingClientRect();
    const to = target.getBoundingClientRect();
    const event = (type, clientX, clientY) => new MouseEvent(type, {
      bubbles: true, button: 0, clientX, clientY,
    });
    handle.dispatchEvent(event('mousedown', from.left + from.width / 2, from.top + from.height / 2));
    document.dispatchEvent(event('mousemove', to.left + 8, Math.max(1, to.top - 8)));
    document.dispatchEvent(event('mouseup', to.left + 8, Math.max(1, to.top - 8)));
  }, { sourceId, targetId });
}

test('unfiled chats reveal five at a time above Projects without a nested scroller', async ({ page }) => {
  const state = {
    sessions: [
      ...Array.from({ length: 18 }, (_, index) => sessionFixture(
        `chat-${String(index).padStart(2, '0')}`,
        `Recent chat ${index + 1}`,
        { minutes: index },
      )),
      sessionFixture('project-alpha', 'Alpha project chat', { projectId: 'project-alpha', minutes: 30 }),
      sessionFixture('project-beta', 'Beta project chat', { projectId: 'project-beta', minutes: 31 }),
    ],
    projects: [projectFixture('project-alpha', 'Alpha'), projectFixture('project-beta', 'Beta')],
    remoteOrder: null,
    prefStatus: 200,
    puts: [],
  };
  await page.setViewportSize({ width: 1280, height: 500 });
  await mockShell(page, state);
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getSessions?.().length)).toBe(20);

  const unfiled = page.locator('#session-list > .session-unfiled-region');
  await expect(unfiled).toHaveAttribute('role', 'group');
  await expect(unfiled).toHaveAttribute('aria-label', 'Recent chats');
  await expect(page.locator('#session-list .unsorted-folder')).toHaveCount(0);
  await expect(page.locator('#session-list .session-folder-header .folder-name', { hasText: /^Chats$/ })).toHaveCount(0);
  await expect(unfiled.locator('.session-item')).toHaveCount(5);

  const topLevelOrder = await page.locator('#session-list').evaluate(list => (
    Array.from(list.children).map(child => {
      if (child.classList.contains('session-unfiled-region')) return 'recent';
      if (child.classList.contains('sidebar-nav-label')) return `label:${child.textContent.trim()}`;
      if (child.classList.contains('session-folder')) return `project:${child.dataset.projectId}`;
      return child.className;
    })
  ));
  expect(topLevelOrder.slice(0, 2)).toEqual(['recent', 'label:Projects']);

  // Flush left: no reserved drag-handle gutter. The project folder icon lines
  // up with the Projects label, and a nested chat keeps only the folder indent.
  const labelBox = await page.locator('#session-list .sidebar-nav-label-row > span').first().boundingBox();
  const folderBox = await page.locator('.session-folder[data-project-id="project-alpha"] .folder-icon').boundingBox();
  const chatBox = await page.locator('.session-folder[data-project-id="project-alpha"] .session-star').first().boundingBox();
  expect(Math.abs(folderBox.x - labelBox.x)).toBeLessThanOrEqual(2);
  expect(Math.round(chatBox.x - folderBox.x)).toBe(22);

  for (const count of [10, 15, 18]) {
    await unfiled.getByRole('button', { name: 'Show more' }).click();
    await expect(unfiled.locator('.session-item')).toHaveCount(count);
  }
  const bounded = await unfiled.evaluate(region => ({
    clientHeight: region.clientHeight,
    scrollHeight: region.scrollHeight,
    overflowY: getComputedStyle(region).overflowY,
    viewportHeight: window.innerHeight,
  }));
  expect(bounded.overflowY).toBe('visible');
  expect(bounded.scrollHeight).toBe(bounded.clientHeight);

  await page.setViewportSize({ width: 390, height: 640 });
  if (await page.locator('#sidebar').evaluate(sidebar => sidebar.classList.contains('hidden'))) {
    await page.locator('#hamburger-btn').click();
  }
  await expect(unfiled).toBeVisible();
  const narrow = await unfiled.evaluate(region => ({
    clientWidth: region.clientWidth,
    scrollWidth: region.scrollWidth,
  }));
  expect(narrow.scrollWidth).toBeLessThanOrEqual(narrow.clientWidth + 1);

  state.sessions = [];
  state.projects = [];
  await page.evaluate(async () => {
    const projects = await import('/static/js/projects.js');
    await projects.refreshProjects();
    await window.sessionModule.loadSessions();
  });
  await expect(page.locator('#session-list .session-unfiled-region, #session-list .session-folder')).toHaveCount(0);
  await expect(page.locator('#session-list .sidebar-nav-label-row')).toHaveCount(1);
});

test('pointer and keyboard project reorder persist through the authenticated preference API', async ({ browser, page }) => {
  const state = {
    sessions: [
      sessionFixture('alpha', 'Alpha chat', { projectId: 'project-alpha' }),
      sessionFixture('beta', 'Beta chat', { projectId: 'project-beta' }),
      sessionFixture('gamma', 'Gamma chat', { projectId: 'project-gamma' }),
      sessionFixture('loose', 'Unfiled chat'),
    ],
    projects: [
      projectFixture('project-alpha', 'Alpha'),
      projectFixture('project-beta', 'Beta'),
      projectFixture('project-gamma', 'Gamma'),
    ],
    remoteOrder: ['project-gamma', 'project-alpha', 'project-beta'],
    prefStatus: 200,
    puts: [],
  };
  await page.addInitScript(() => {
    localStorage.setItem('odysseus-folder-order', JSON.stringify(['project-beta', 'project-alpha', 'project-gamma']));
    localStorage.setItem('odysseus-folder-state', JSON.stringify({ 'project-alpha': false, 'project-beta': false, 'project-gamma': false }));
  });
  await mockShell(page, state);
  await page.goto('/static/index.html');
  await expect.poll(() => projectOrder(page)).toEqual(['project-gamma', 'project-alpha', 'project-beta']);

  const gammaHandle = page.locator('.session-folder[data-project-id="project-gamma"] .folder-drag-handle');
  await expect(gammaHandle).toHaveAttribute('role', 'button');
  await expect(gammaHandle).toHaveAttribute('aria-keyshortcuts', 'Alt+ArrowUp Alt+ArrowDown');
  await gammaHandle.focus();
  await gammaHandle.press('Alt+ArrowDown');
  await expect.poll(() => projectOrder(page)).toEqual(['project-alpha', 'project-gamma', 'project-beta']);
  await expect(page.locator('.session-folder[data-project-id="project-gamma"] .folder-drag-handle')).toBeFocused();
  await expect.poll(() => state.puts.at(-1)?.value).toEqual(['project-alpha', 'project-gamma', 'project-beta']);

  await expect(page.locator('#app-loader')).toHaveCount(0);
  const betaHandle = page.locator('.session-folder[data-project-id="project-beta"] .folder-drag-handle');
  const betaFolder = page.locator('.session-folder[data-project-id="project-beta"]');
  const alphaFolder = page.locator('.session-folder[data-project-id="project-alpha"]');
  const betaBox = await betaHandle.boundingBox();
  const alphaBox = await alphaFolder.boundingBox();
  expect(betaBox).not.toBeNull();
  expect(alphaBox).not.toBeNull();
  await page.mouse.move(betaBox.x + betaBox.width / 2, betaBox.y + betaBox.height / 2);
  expect(await page.evaluate(({ x, y }) => (
    document.elementFromPoint(x, y)?.closest('.folder-drag-handle')?.getAttribute('aria-label')
  ), { x: betaBox.x + betaBox.width / 2, y: betaBox.y + betaBox.height / 2 })).toContain('Beta');
  await page.mouse.down();
  await expect(betaFolder).toHaveClass(/dragging/);
  await page.mouse.move(alphaBox.x + 8, Math.max(1, alphaBox.y - 8), { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => projectOrder(page)).toEqual(['project-beta', 'project-alpha', 'project-gamma']);
  await expect.poll(() => state.puts.at(-1)?.value).toEqual(['project-beta', 'project-alpha', 'project-gamma']);

  // A separate browser context has no localStorage from the first page; its
  // order therefore proves the authenticated preference readback path.
  const secondContext = await browser.newContext();
  const secondPage = await secondContext.newPage();
  await mockShell(secondPage, state);
  await secondPage.goto('/static/index.html');
  await expect.poll(() => projectOrder(secondPage)).toEqual(['project-beta', 'project-alpha', 'project-gamma']);
  await secondContext.close();
});

test('local project order remains usable when preference storage is unavailable', async ({ page }) => {
  const state = {
    sessions: [
      sessionFixture('alpha', 'Alpha chat', { projectId: 'project-alpha' }),
      sessionFixture('beta', 'Beta chat', { projectId: 'project-beta' }),
    ],
    projects: [projectFixture('project-alpha', 'Alpha'), projectFixture('project-beta', 'Beta')],
    remoteOrder: null,
    prefStatus: 503,
    puts: [],
  };
  await page.addInitScript(() => {
    if (!localStorage.getItem('odysseus-folder-order')) {
      localStorage.setItem('odysseus-folder-order', JSON.stringify(['project-beta', 'project-alpha']));
    }
  });
  await mockShell(page, state);
  await page.goto('/static/index.html');
  await expect.poll(() => projectOrder(page)).toEqual(['project-beta', 'project-alpha']);

  await page.locator('.session-folder[data-project-id="project-beta"] .folder-drag-handle').press('Alt+ArrowDown');
  await expect.poll(() => projectOrder(page)).toEqual(['project-alpha', 'project-beta']);
  await expect.poll(() => page.evaluate(() => (
    JSON.parse(localStorage.getItem('odysseus-folder-order') || '[]')
  ))).toEqual(['project-alpha', 'project-beta']);

  await page.reload();
  await expect.poll(() => projectOrder(page)).toEqual(['project-alpha', 'project-beta']);
});

test('pinned and unfiled drag sorting preserve one combined session order', async ({ page }) => {
  const state = {
    sessions: [
      sessionFixture('pin-new', 'Pinned new', { pinned: true, minutes: 0 }),
      sessionFixture('pin-old', 'Pinned old', { pinned: true, minutes: 10 }),
      sessionFixture('loose-new', 'Loose new', { minutes: 0 }),
      sessionFixture('loose-old', 'Loose old', { minutes: 10 }),
    ],
    projects: [],
    remoteOrder: null,
    prefStatus: 200,
    puts: [],
  };
  await page.addInitScript(() => {
    if (!localStorage.getItem('odysseus-session-sort')) {
      localStorage.setItem('odysseus-session-sort', 'group');
    }
    if (!localStorage.getItem('session-order')) {
      localStorage.setItem('session-order', JSON.stringify([
        'pin-old', 'loose-old', 'pin-new', 'loose-new',
      ]));
    }
  });
  await mockShell(page, state);
  await page.goto('/static/index.html');
  await expect(page.locator('#session-list .item-drag-handle').first()).toBeAttached();

  const ids = selector => page.locator(selector).evaluateAll(
    items => items.map(item => item.dataset.sessionId),
  );
  await expect.poll(() => ids('#session-list > .list-item[data-session-id]'))
    .toEqual(['pin-old', 'pin-new']);
  await dragBefore(page, 'pin-new', 'pin-old');
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('session-order'))))
    .toEqual(['pin-new', 'loose-old', 'pin-old', 'loose-new']);

  await dragBefore(page, 'loose-new', 'loose-old');
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('session-order'))))
    .toEqual(['pin-new', 'loose-new', 'pin-old', 'loose-old']);

  await page.reload();
  await expect.poll(() => ids('#session-list > .list-item[data-session-id]'))
    .toEqual(['pin-new', 'pin-old']);
  await expect.poll(() => ids('#session-unfiled-region .list-item[data-session-id]'))
    .toEqual(['loose-new', 'loose-old']);
});

test('project chats reveal five at a time and retain drag order over activity sorting on another device', async ({ browser, page }) => {
  const state = {
    sessions: Array.from({ length: 12 }, (_, index) => sessionFixture(`folder-${index}`, `Project chat ${index}`, { projectId: 'project-alpha', minutes: index })),
    projects: [projectFixture('project-alpha', 'Alpha')],
    remoteOrder: null, prefStatus: 200, puts: [],
  };
  await mockShell(page, state);
  await page.goto('/static/index.html');
  const rows = page.locator('.session-folder-content .session-item');
  await expect(rows).toHaveCount(5);
  await page.locator('.session-show-more-btn').click();
  await expect(rows).toHaveCount(10);
  await page.locator('.session-show-more-btn').click();
  await expect(rows).toHaveCount(12);
  await expect(page.locator('#session-list .item-drag-handle').first()).toBeAttached();
  await page.locator('.list-item[data-session-id="folder-2"]').hover();
  await dragBefore(page, 'folder-2', 'folder-0');
  await expect.poll(() => state.sessionOrder?.[0]).toBe('folder-2');
  const context = await browser.newContext();
  const other = await context.newPage();
  await mockShell(other, state);
  await other.goto('/static/index.html');
  await expect(other.locator('.session-folder-content .session-item').first()).toContainText('Project chat 2');
  await other.locator('.session-folder-content .session-item').first().focus();
  await other.locator('.session-folder-content .session-item').first().press('Alt+ArrowDown');
  await expect(other.locator('.session-folder-content .session-item').first()).toContainText('Project chat 0');
  await expect.poll(() => state.sessionOrder?.[0]).toBe('folder-0');
  await context.close();
});

test('a slow sidebar preference read cannot replace an explicit task selection', async ({ page }) => {
  const state = { sessions: [sessionFixture('alpha', 'Alpha chat'), sessionFixture('beta', 'Beta chat')], projects: [], remoteOrder: null, prefStatus: 200, puts: [] };
  await mockShell(page, state);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/prefs/sidebar-session-order', async route => {
    await gate;
    return route.fulfill({ json: { value: null } });
  });
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule))).toBe(true);
  await page.evaluate(() => window.sessionModule.selectSession('beta', { showLoading: false }));
  release();
  await expect.poll(() => page.evaluate(() => window.sessionModule.getSessions().length)).toBe(2);
  await expect.poll(() => page.evaluate(() => window.sessionModule.getCurrentSessionId())).toBe('beta');
});


test('project folders stay global across agent views and order persists on reload', async ({ page }) => {
  const state = {
    sessions: [
      sessionFixture('a', 'A chat', { projectId: 'project-a' }),
      sessionFixture('b', 'B chat', { projectId: 'project-b' }),
      { ...sessionFixture('c', 'C chat', { projectId: 'project-c' }), agent_target: 'hermes' },
      { ...sessionFixture('d', 'D chat', { projectId: 'project-d' }), agent_target: 'hermes' },
    ],
    projects: [
      projectFixture('project-a', 'A'),
      projectFixture('project-b', 'B'),
      projectFixture('project-c', 'C'),
      projectFixture('project-d', 'D'),
    ],
    remoteOrder: ['project-b', 'project-a', 'project-d', 'project-c'], prefStatus: 200, puts: [],
    catalog: {
      discovery: { schema_version: 'pandamonium.discovery.v1', entities: ['jarvis', 'hermes'].map(target => ({ id: `agent:${target}`, kind: 'agent', display_name: target })) },
      selections: ['jarvis', 'hermes'].map(target => ({ entity_id: `agent:${target}`, target, kind: 'agent', capabilities: ['model'], selectable: true })),
    },
  };
  await mockShell(page, state);
  await page.goto('/static/index.html');
  const activate = id => page.evaluate(async sessionId => {
    const module = await import('/static/js/sessions.js');
    module.setCurrentSessionId(sessionId);
    module.updateModelPicker();
  }, id);
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getSessions?.().length)).toBe(4);
  await activate('a');
  // Projects are installation-wide real folders, so every agent view shows the
  // same ordered project list even when its chats belong to another agent.
  await expect.poll(() => projectOrder(page)).toEqual(['project-b', 'project-a', 'project-d', 'project-c']);
  await page.locator('[data-project-id="project-b"] .folder-drag-handle').press('Alt+ArrowDown');
  await expect.poll(() => state.remoteOrder).toEqual(['project-a', 'project-b', 'project-d', 'project-c']);
  await activate('c');
  await expect.poll(() => projectOrder(page)).toEqual(['project-a', 'project-b', 'project-d', 'project-c']);
  await page.locator('[data-project-id="project-d"] .folder-drag-handle').press('Alt+ArrowDown');
  await expect.poll(() => state.remoteOrder).toEqual(['project-a', 'project-b', 'project-c', 'project-d']);
  await page.reload();
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getSessions?.().length)).toBe(4);
  await activate('a');
  await expect.poll(() => projectOrder(page)).toEqual(['project-a', 'project-b', 'project-c', 'project-d']);
  await activate('c');
  await expect.poll(() => projectOrder(page)).toEqual(['project-a', 'project-b', 'project-c', 'project-d']);
});
