// Real HTTP + dedicated PostgreSQL + deterministic AI. No Qwen/E5/BGE inference.
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import type { Run, Task } from '../src/api/types';

const CARD_TEXT =
  'Invented tray exercise: place one teal card in tray T42, then count seven paper circles. Record the tray label before counting the paper circles.';
function account(label: 'alice' | 'bob') {
  const path = process.env.RESEARCHFLOW_E2E_CREDENTIALS;
  if (!path) throw new Error('Run scripts.validate_web with its dedicated disposable database.');
  return JSON.parse(readFileSync(path, 'utf8'))[label] as { username: string; password: string };
}
async function authenticate(page: Page, label: 'alice' | 'bob' = 'alice') {
  const user = account(label);
  const hash = page.url() === 'about:blank' ? '' : new URL(page.url()).hash;
  await page.goto('/' + hash);
  await page.getByLabel('用户名', { exact: true }).fill(user.username);
  await page.getByLabel('密码', { exact: true }).fill(user.password);
  const pending = page.waitForResponse((r) => r.url().endsWith('/api/v1/auth/login'));
  await page.getByRole('button', { name: '进入科研工作台' }).click();
  const response = await pending;
  expect(response.status()).toBe(200);
  const token = (await response.json()).access_token as string;
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  await expect(page.getByText('数据库已就绪', { exact: true })).toBeVisible();
  return token;
}
async function logout(page: Page) {
  await page.getByRole('button', { name: '注销' }).filter({ visible: true }).click();
  await expect(page.getByRole('heading', { name: '欢迎回到工作台' })).toBeVisible();
}
async function upload(
  page: Page,
  key = 'p4-cards-' + randomUUID(),
  content = CARD_TEXT,
  expectedStatus = 201,
) {
  await page.getByRole('link', { name: '科研知识库', exact: true }).click();
  await page.getByLabel('文档标题', { exact: true }).fill('Invented tray cards ' + key);
  await page.getByLabel('文档 Key', { exact: true }).fill(key);
  await page.getByLabel('TXT / Markdown 文件', { exact: false }).setInputFiles({
    name: 'invented-tray-cards.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(content),
  });
  const pending = page.waitForResponse(
    (r) => r.url().endsWith('/api/v1/documents') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '上传到我的知识库' }).click();
  const response = await pending;
  expect(response.status()).toBe(expectedStatus);
  return { key, ...(await response.json()) } as {
    key: string;
    document_id: string;
    version_id: string;
    is_duplicate: boolean;
  };
}
async function indexed(page: Page) {
  const document = await upload(page);
  await page
    .getByRole('row')
    .filter({ hasText: document.key })
    .getByRole('button', { name: '查看详情' })
    .click();
  await page.getByRole('button', { name: '建立 / 检查索引' }).click();
  await expect(page.locator('.detail-panel .badge').filter({ hasText: '已完成' })).toBeVisible();
  return document;
}
async function workflow(
  page: Page,
  key: string,
  query = 'Create tasks for the teal card in tray T42',
) {
  await page.getByRole('link', { name: 'Agent 工作流', exact: true }).click();
  await expect(page.getByText('MOCK · 当前是确定性测试推理，不是真实模型生成。')).toBeVisible();
  await page.getByRole('combobox', { name: '文档范围', exact: true }).selectOption(key);
  await page.getByLabel('研究任务要求', { exact: true }).fill(query);
  const pending = page.waitForResponse(
    (r) => r.url().endsWith('/api/v1/agent/runs') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '生成任务草案' }).click();
  const response = await pending;
  expect(response.status()).toBe(201);
  return { run: (await response.json()) as Run, request: response.request() };
}
async function decide(page: Page, choice: 'APPROVE' | 'REJECT') {
  await expect(page.locator('.workflow-detail')).toContainText('等待人工审批');
  await page
    .getByRole('button', { name: choice === 'APPROVE' ? '审阅并批准' : '拒绝草案', exact: true })
    .click();
  const pending = page.waitForResponse((r) => r.url().endsWith('/decisions'));
  await page
    .getByRole('dialog')
    .getByRole('button', {
      name: choice === 'APPROVE' ? '确认批准并写入' : '确认拒绝',
      exact: true,
    })
    .click();
  const response = await pending;
  expect(response.status()).toBe(200);
  return { run: (await response.json()) as Run, request: response.request() };
}
async function tasks(page: Page, token: string, runId: string) {
  const response = await page.request.get('/api/v1/tasks?limit=100', {
    headers: { Authorization: 'Bearer ' + token },
  });
  expect(response.status()).toBe(200);
  return ((await response.json()).items as Task[]).filter((task) => task.workflow_id === runId);
}

test('real login errors, memory-only session and server-side logout revocation', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByLabel('用户名', { exact: true }).fill(account('alice').username);
  await page.getByLabel('密码', { exact: true }).fill('invented-invalid-password');
  await page.getByRole('button', { name: '进入科研工作台' }).click();
  await expect(page.getByRole('alert')).toContainText('登录已失效或凭据不正确');
  const token = await authenticate(page);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await logout(page);
  expect(
    (
      await page.request.get('/api/v1/auth/me', { headers: { Authorization: 'Bearer ' + token } })
    ).status(),
  ).toBe(401);
});

test('synthetic upload, duplicate detection, deterministic index and grounded MOCK RAG', async ({
  page,
}) => {
  await authenticate(page);
  const document = await indexed(page);
  const repeat = await upload(page, document.key, CARD_TEXT, 200);
  expect(repeat.is_duplicate).toBe(true);
  expect(repeat.version_id).toBe(document.version_id);
  await page.getByRole('link', { name: '智能问答', exact: true }).click();
  await page.getByRole('combobox', { name: '文档范围', exact: true }).selectOption(document.key);
  await page.getByLabel('科研问题', { exact: true }).fill('Which tray receives the teal card?');
  const pending = page.waitForResponse((r) => r.url().endsWith('/api/v1/rag/answer'));
  await page.getByRole('button', { name: '提交问题 →' }).click();
  const response = await pending;
  expect(response.status()).toBe(200);
  const answer = await response.json();
  expect(answer.status).toBe('answered');
  expect(answer.is_mock).toBe(true);
  expect(answer.citations).toHaveLength(1);
  expect(answer.citations[0].document_key).toBe(document.key);
  expect(answer.citations[0].version_id).toBe(document.version_id);
  expect(CARD_TEXT).toContain(answer.citations[0].excerpt);
  await expect(page.getByText('MOCK · 非真实模型结果', { exact: true })).toBeVisible();
  await page.locator('.sources summary').click();
  await expect(page.locator('.sources blockquote')).toContainText('teal card');
});

test('approval survives reload, creation and decision replay have exactly one task', async ({
  page,
}) => {
  let token = await authenticate(page);
  const document = await indexed(page);
  const first = await workflow(page, document.key);
  expect(first.run.status).toBe('AWAITING_APPROVAL');
  expect(await tasks(page, token, first.run.run_id)).toHaveLength(0);
  const headers = {
    Authorization: 'Bearer ' + token,
    'Idempotency-Key': first.request.headers()['idempotency-key']!,
    'X-Workflow-Token': first.run.workflow_token!,
  };
  const duplicate = await page.request.post('/api/v1/agent/runs', {
    headers,
    data: first.request.postDataJSON(),
  });
  // The existing route uses 201 for both outcomes; the body distinguishes reuse.
  expect(duplicate.status()).toBe(201);
  const duplicateRun = await duplicate.json();
  expect(duplicateRun.run_id).toBe(first.run.run_id);
  expect(duplicateRun.created).toBe(false);
  await page.reload();
  await expect(page.getByRole('heading', { name: '欢迎回到工作台' })).toBeVisible();
  token = await authenticate(page);
  await expect(page.locator('.run-id')).toHaveText(first.run.run_id);
  const approved = await decide(page, 'APPROVE');
  expect(approved.run.status).toBe('COMPLETED');
  const replay = await page.request.post(`/api/v1/agent/runs/${first.run.run_id}/decisions`, {
    headers: {
      Authorization: 'Bearer ' + token,
      'X-Workflow-Token': approved.request.headers()['x-workflow-token']!,
    },
    data: approved.request.postDataJSON(),
  });
  expect(replay.status()).toBe(200);
  expect((await replay.json()).result).toEqual(approved.run.result);
  expect(await tasks(page, token, first.run.run_id)).toHaveLength(1);
  await page.getByRole('button', { name: '查看正式任务 →' }).click();
  await expect(page.locator('.content')).toContainText('[MOCK] Check tray label');
});

test('editing creates a new review version and only the new approval writes a task', async ({
  page,
}) => {
  const token = await authenticate(page);
  const document = await indexed(page);
  const created = await workflow(page, document.key);
  await page.getByRole('button', { name: '修改后重新审阅', exact: true }).click();
  const title = '[MOCK] Recheck the invented teal card tray label';
  await page.getByRole('dialog').getByLabel('任务 1 标题').fill(title);
  const pending = page.waitForResponse((r) => r.url().endsWith('/decisions'));
  await page.getByRole('button', { name: '保存修改并重新审阅', exact: true }).click();
  const response = await pending;
  expect(response.status()).toBe(200);
  const revised = (await response.json()) as Run;
  expect(revised.status).toBe('AWAITING_APPROVAL');
  expect(revised.proposal!.version).toBe(created.run.proposal!.version + 1);
  expect(revised.proposal!.content_digest).not.toBe(created.run.proposal!.content_digest);
  expect(await tasks(page, token, created.run.run_id)).toHaveLength(0);
  const stale = await page.request.post(`/api/v1/agent/runs/${created.run.run_id}/decisions`, {
    headers: { Authorization: 'Bearer ' + token, 'X-Workflow-Token': created.run.workflow_token! },
    data: {
      proposal_id: created.run.proposal!.id,
      proposal_version: created.run.proposal!.version,
      content_digest: created.run.proposal!.content_digest,
      decision: 'APPROVE',
    },
  });
  expect(stale.status()).toBe(409);
  expect(await tasks(page, token, created.run.run_id)).toHaveLength(0);
  expect((await decide(page, 'APPROVE')).run.status).toBe('COMPLETED');
  const written = await tasks(page, token, created.run.run_id);
  expect(written).toHaveLength(1);
  expect(written[0]!.title).toBe(title);
});

test('human rejection creates no task and remains rejected after reload', async ({ page }) => {
  let token = await authenticate(page);
  const document = await indexed(page);
  const created = await workflow(page, document.key);
  expect((await decide(page, 'REJECT')).run.status).toBe('REJECTED');
  expect(await tasks(page, token, created.run.run_id)).toHaveLength(0);
  await page.reload();
  token = await authenticate(page);
  await expect(page.locator('.workflow-detail')).toContainText('已拒绝');
  await expect(page.getByRole('button', { name: '审阅并批准', exact: true })).toHaveCount(0);
  expect(await tasks(page, token, created.run.run_id)).toHaveLength(0);
});

test('other user cannot access document, checkpoint, capability, approval or task', async ({
  page,
}) => {
  const owner = await authenticate(page);
  const document = await indexed(page);
  const created = await workflow(page, document.key);
  const approved = await decide(page, 'APPROVE');
  const [task] = await tasks(page, owner, created.run.run_id);
  if (!task) throw new Error('Approval did not persist a task');
  await logout(page);
  const other = await authenticate(page, 'bob');
  await page.getByRole('link', { name: '科研知识库', exact: true }).click();
  await expect(page.getByRole('row').filter({ hasText: document.key })).toHaveCount(0);
  const headers = {
    Authorization: 'Bearer ' + other,
    'X-Workflow-Token': created.run.workflow_token!,
  };
  for (const path of [
    `/api/v1/documents/${document.document_id}/versions`,
    `/api/v1/documents/${document.document_id}/versions/${document.version_id}/chunks`,
    `/api/v1/agent/runs/${created.run.run_id}`,
    `/api/v1/tasks/${task.id}`,
  ])
    expect((await page.request.get(path, { headers })).status()).toBe(404);
  expect(
    (
      await page.request.post(`/api/v1/agent/runs/${created.run.run_id}/access`, { headers })
    ).status(),
  ).toBe(404);
  expect(
    (
      await page.request.post(`/api/v1/agent/runs/${created.run.run_id}/decisions`, {
        headers,
        data: approved.request.postDataJSON(),
      })
    ).status(),
  ).toBe(404);
  const runs = await page.request.get('/api/v1/agent/runs?limit=100', { headers });
  expect((await runs.json()).items.some((run: Run) => run.run_id === created.run.run_id)).toBe(
    false,
  );
  expect(await tasks(page, other, created.run.run_id)).toHaveLength(0);
});

test('controlled MOCK timeout resumes same persisted workflow and still requires approval', async ({
  page,
}) => {
  let token = await authenticate(page);
  const document = await indexed(page);
  const created = await workflow(
    page,
    document.key,
    'Create tasks P4_RECOVERY_ONCE ' + randomUUID(),
  );
  expect(created.run.status).toBe('FAILED');
  expect(created.run.error_code).toBe('agent_provider_timeout');
  expect(await tasks(page, token, created.run.run_id)).toHaveLength(0);
  await page.reload();
  token = await authenticate(page);
  await expect(page.locator('.workflow-detail')).toContainText('执行失败');
  const pending = page.waitForResponse((r) => r.url().endsWith('/resume'));
  await page.getByRole('button', { name: '恢复此工作流（不新建）' }).click();
  const response = await pending;
  expect(response.status()).toBe(200);
  const run = await response.json();
  expect(run.run_id).toBe(created.run.run_id);
  expect(run.status).toBe('AWAITING_APPROVAL');
  expect(await tasks(page, token, created.run.run_id)).toHaveLength(0);
  expect((await decide(page, 'APPROVE')).run.status).toBe('COMPLETED');
  expect(await tasks(page, token, created.run.run_id)).toHaveLength(1);
});

test('real invalid UTF-8 response is visible and subsequent valid upload succeeds', async ({
  page,
}) => {
  await authenticate(page);
  await page.getByRole('link', { name: '科研知识库', exact: true }).click();
  const key = 'p4-invalid-' + randomUUID();
  await page.getByLabel('文档标题', { exact: true }).fill('Invented invalid encoding');
  await page.getByLabel('文档 Key', { exact: true }).fill(key);
  await page.getByLabel('TXT / Markdown 文件', { exact: false }).setInputFiles({
    name: 'invalid.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from([0xff, 0xfe]),
  });
  const pending = page.waitForResponse(
    (r) => r.url().endsWith('/api/v1/documents') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '上传到我的知识库' }).click();
  const response = await pending;
  expect(response.status()).toBe(400);
  expect((await response.json()).error.code).toBe('invalid_document');
  await expect(page.getByRole('alert')).toContainText('invalid_document');
  await expect(page.getByRole('alert')).toContainText('请求 ID');
  await expect(page.getByRole('row').filter({ hasText: key })).toHaveCount(0);
  await upload(page, key);
  await expect(page.getByRole('row').filter({ hasText: key })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});
