import { expect, test } from '@playwright/test';

const model = {
  endpoint_id: 'endpoint-one',
  endpoint_url: 'http://model.test/v1/chat/completions',
  model: 'test/model',
};

function sessionFixture() {
  return {
    id: 'session-one',
    name: 'Existing chat',
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 0,
    archived: false,
    created_at: '2026-09-04T20:00:00Z',
    updated_at: '2026-09-04T20:01:00Z',
    last_message_at: '2026-09-04T20:01:00Z',
  };
}

async function mockQueueApp(page) {
  const state = {
    streamCalls: 0,
    messages: [],
    release: null,
  };
  const gate = new Promise(resolve => { state.release = resolve; });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') {
      return route.fulfill({ json: [sessionFixture()] });
    }
    if (url.pathname === '/api/history/session-one') {
      return route.fulfill({
        json: {
          history: [],
          model: 'test/model',
          name: 'Existing chat',
          endpoint_url: 'http://model.test/v1/chat/completions',
          offset: 0,
          limit: 100,
          total: 0,
          has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/chat_stream') {
      const body = await route.request().postDataBuffer();
      const message = body?.toString().match(/name="message"\r\n\r\n([^\r]*)/)?.[1] || '';
      state.messages.push(message);
      state.streamCalls += 1;
      if (state.streamCalls === 1) await gate;
      try {
        return await route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
          body: 'data: {"delta":"ok"}\n\n'
            + 'data: [DONE]\n\n',
        });
      } catch {
        return undefined;
      }
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({ json: model });
    }
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: false, privileges: {} } });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
  return state;
}

async function openQueuePage(page) {
  await page.goto('/static/index.html#session-one');
  await expect.poll(() => page.evaluate(() => (
    typeof document.querySelector('#chat-form')?.onsubmit === 'function'
  ))).toBe(true);
  await expect.poll(
    () => page.evaluate(() => (
      typeof window.sessionModule?.selectSession === 'function'
      && window.sessionModule.getSessions?.().some(session => session.id === 'session-one')
    )),
    { timeout: 15_000 },
  ).toBe(true);
  await page.evaluate(() => window.sessionModule.selectSession('session-one', { showLoading: false }));
  await expect.poll(
    () => page.evaluate(() => window.sessionModule?.getCurrentSessionId()),
    { timeout: 15_000 },
  ).toBe('session-one');
}

async function sendMessage(page, text) {
  await page.locator('#message:visible').fill(text);
  await page.locator('.send-btn:visible').click();
}

async function queueMessage(page, text) {
  const items = page.locator('#chat-queue-strip .chat-queue-item');
  const before = await items.count();
  await page.locator('#message:visible').fill(text);
  await page.locator('.send-btn:visible').click();
  await expect(items).toHaveCount(before + 1);
  await expect(items.last().locator('.chat-queue-text')).toHaveText(text);
  // The submit path debounces rapid submissions for 300ms; let it settle so
  // the next queue/steer action is not swallowed.
  await page.waitForTimeout(350);
}

async function startStreaming(page) {
  await sendMessage(page, 'Run whoami');
  await expect(page.locator('.send-btn:visible')).toHaveAttribute('data-mode', 'streaming');
}

test('queued messages dock above the composer instead of the chat history', async ({ page }) => {
  const state = await mockQueueApp(page);
  await openQueuePage(page);
  await startStreaming(page);

  await queueMessage(page, 'Second question');

  const strip = page.locator('#chat-queue-strip');
  await expect(strip).toBeVisible();
  await expect(strip.locator('.chat-queue-item')).toHaveCount(1);
  await expect(strip.locator('.chat-queue-text')).toHaveText('Second question');
  await expect(strip.locator('.chat-queue-steer')).toBeVisible();
  await expect(strip.locator('.chat-queue-more')).toBeVisible();

  const geometry = await page.evaluate(() => {
    const stripEl = document.querySelector('#chat-queue-strip');
    const bar = document.querySelector('.chat-input-bar');
    const top = document.querySelector('.chat-input-top');
    return {
      insideBar: bar.contains(stripEl),
      stripTop: stripEl.getBoundingClientRect().top,
      topTop: top.getBoundingClientRect().top,
    };
  });
  expect(geometry.insideBar).toBe(true);
  expect(geometry.stripTop).toBeLessThan(geometry.topTop);

  await expect(page.locator('#chat-history .msg-user-queued, #chat-history .chat-queued-bubble-host')).toHaveCount(0);
  await expect(page.locator('#chat-history .msg-user')).toHaveCount(1);
  await expect(page.locator('#chat-history')).not.toContainText('Second question');
  state.release();
});

test('queue card menu edits and deletes items without reordering', async ({ page }) => {
  const state = await mockQueueApp(page);
  await openQueuePage(page);
  await startStreaming(page);

  await queueMessage(page, 'First queued');
  await queueMessage(page, 'Second queued');

  const cards = page.locator('#chat-queue-strip .chat-queue-item');
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0).locator('.chat-queue-text')).toHaveText('First queued');

  await cards.nth(0).locator('.chat-queue-more').click();
  await expect(cards.nth(0).locator('.chat-queue-menu')).toBeVisible();
  await cards.nth(0).locator('[data-queue-action="edit"]').click();

  const editInput = cards.nth(0).locator('.chat-queue-edit-input');
  await expect(editInput).toBeVisible();
  await expect(editInput).toHaveValue('First queued');
  await editInput.fill('First edited');
  await editInput.press('Enter');

  await expect(cards.nth(0).locator('.chat-queue-text')).toHaveText('First edited');
  await expect(editInput).toBeHidden();
  await expect(cards.nth(1).locator('.chat-queue-text')).toHaveText('Second queued');

  await cards.nth(1).locator('.chat-queue-more').click();
  await cards.nth(1).locator('[data-queue-action="delete"]').click();

  await expect(cards).toHaveCount(1);
  await expect(cards.nth(0).locator('.chat-queue-text')).toHaveText('First edited');
  state.release();
});

test('Steer sends the queued message immediately and stops the active response', async ({ page }) => {
  const state = await mockQueueApp(page);
  await openQueuePage(page);
  await startStreaming(page);

  await queueMessage(page, 'Steer me');
  const steer = page.locator('#chat-queue-strip .chat-queue-item .chat-queue-steer');
  await expect(steer).toBeVisible();
  await steer.click();

  await expect(page.locator('#chat-queue-strip .chat-queue-item')).toHaveCount(0);
  await expect(page.locator('#chat-queue-strip')).toBeHidden();
  await expect.poll(() => state.streamCalls, { timeout: 10_000 }).toBe(2);
  expect(state.messages[1]).toBe('Steer me');
  state.release();
});

test('stream completion drains queued messages in order', async ({ page }) => {
  const state = await mockQueueApp(page);
  await openQueuePage(page);
  await startStreaming(page);

  await queueMessage(page, 'Drain me');
  await expect(page.locator('#chat-queue-strip .chat-queue-item')).toHaveCount(1);

  state.release();
  await expect.poll(() => state.streamCalls, { timeout: 10_000 }).toBe(2);
  expect(state.messages).toEqual(['Run whoami', 'Drain me']);
  await expect(page.locator('#chat-queue-strip .chat-queue-item')).toHaveCount(0);
  await expect(page.locator('#chat-queue-strip')).toBeHidden();
});
