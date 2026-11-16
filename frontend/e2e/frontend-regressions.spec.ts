// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import type { ModelDetail, GovernedModel } from '../types/governedMetrics';

// Authentication and the application shell are real. Large metric/query payloads
// are deterministic browser fixtures, not a claim of production LLM validation.
const payload = JSON.parse(
	readFileSync(new URL('../lib/examples/ordersMetricModel.json', import.meta.url), 'utf8'),
) as GovernedModel;
payload.model_id = 'browser-regression';
payload.name = 'Browser regression model';
payload.metrics = Array.from({ length: 40 }, (_, i) => ({
	...payload.metrics[0],
	metric_id: `metric_${i}`,
	name: `指标${i}：含长中文名称的订单金额标准统计口径`,
	description: '用于验证中文长名称、键盘访问和小屏选择区域。'.repeat(3),
	family_id: `family_${i % 4}`,
}));
const detail: ModelDetail = {
	model_id: payload.model_id,
	name: payload.name,
	version: 1,
	latest_revision: 1,
	active_revision: 1,
	archived: false,
	owner_id: null,
	revisions: [
		{
			revision: 1,
			status: 'certified',
			payload,
			fingerprint: 'browser',
			validation: null,
			certification: null,
			created_by: 'fixture',
			created_at: '2026-01-01',
		},
	],
};
const fixtureApis = async (page: Page) => {
	await page.route('**/api/metrics/config', (route) =>
		route.fulfill({ json: { data: { enabled: true } } }),
	);
	await page.route('**/api/metrics/models', (route) =>
		route.fulfill({ json: { data: [detail] } }),
	);
	await page.route(`**/api/metrics/models/${payload.model_id}`, (route) =>
		route.fulfill({ json: { data: detail } }),
	);
	await page.route('**/api/semantic-compilation/status', (route) =>
		route.fulfill({ json: { calculated: true, running: false } }),
	);
};
const login = async (page: Page) => {
	const email = process.env.AUTO_ONTOLOGY_ADMIN_EMAIL;
	const password = process.env.AUTO_ONTOLOGY_ADMIN_PASSWORD;
	if (!email || !password) throw new Error('E2E requires an isolated fixture admin account.');
	await fixtureApis(page);
	await page.goto('/login?password');
	await page.getByLabel('Email', { exact: true }).fill(email);
	await page.getByLabel('Password', { exact: true }).fill(password);
	await page.getByRole('button', { name: 'Sign in', exact: true }).click();
	await expect(page).toHaveURL(/\/chat/);
	await expect(page.getByRole('button', { name: 'Standard metrics', exact: true })).toBeVisible();
};

test('40 metric variants preserve the composer and horizontal bounds at six effective viewport sizes', async ({
	page,
}, info) => {
	await login(page);
	await page.getByRole('button', { name: 'Standard metrics', exact: true }).click();
	await page.getByLabel('Activated metric model').selectOption(payload.model_id);
	await page.getByText(/Metric variants \(optional\)/).click();
	const results = [];
	// 640x360 is the effective CSS viewport of 1280x720 at 200% browser zoom.
	for (const [width, height] of [
		[1280, 720],
		[1440, 900],
		[1920, 1080],
		[2560, 1440],
		[3840, 2160],
		[640, 360],
	]) {
		await page.setViewportSize({ width, height });
		const search = page.getByLabel('Search metric variants');
		await search.fill('指标39');
		await expect(page.getByRole('checkbox')).toHaveCount(1);
		await page.getByRole('checkbox').check();
		const composer = page.locator('form textarea');
		await composer.fill('保留输入区');
		await expect(
			page.getByRole('button', { name: 'Send message', exact: true }),
		).toBeInViewport();
		const bounds = await page.evaluate(() => ({
			width: innerWidth,
			height: innerHeight,
			scrollWidth: document.documentElement.scrollWidth,
			scrollHeight: document.documentElement.scrollHeight,
		}));
		expect(bounds.scrollWidth).toBeLessThanOrEqual(width + 1);
		expect(bounds.scrollHeight).toBeLessThanOrEqual(height + 1);
		results.push(bounds);
		await search.fill('');
	}
	await info.attach('viewport-measurements', {
		body: JSON.stringify(results),
		contentType: 'application/json',
	});
});

test('form and unapplied JSON survive navigation and reload without automatic certification', async ({
	page,
}) => {
	await login(page);
	await page.goto('/metrics');
	await page.getByRole('button', { name: /^Browser regression model/ }).click();
	await page.getByLabel('Model name', { exact: true }).fill('Recovered browser model');
	await page.getByText('Advanced JSON · typed expressions and full contract').click();
	await page.getByLabel('Model JSON', { exact: true }).fill('{ "unfinished":');
	// Normal client navigation, then return. Recovery is deliberate and versioned.
	await page.getByRole('link', { name: 'Chat', exact: true }).click();
	await page.goBack();
	await page.getByRole('button', { name: /^Browser regression model/ }).click();
	await page.getByRole('button', { name: 'Restore browser draft' }).click();
	await expect(page.getByLabel('Model name', { exact: true })).toHaveValue(
		'Recovered browser model',
	);
	await expect(page.getByLabel('Model JSON', { exact: true })).toHaveValue('{ "unfinished":');
	page.once('dialog', (dialog) => dialog.accept());
	await page.reload();
	await page.getByRole('button', { name: /^Browser regression model/ }).click();
	await page.getByRole('button', { name: 'Restore browser draft' }).click();
	await expect(page.getByLabel('Model JSON', { exact: true })).toHaveValue('{ "unfinished":');
	await expect(page.getByRole('button', { name: 'Save new draft revision' })).toBeDisabled();
	await page.getByRole('button', { name: 'Discard edits', exact: true }).click();
	await page.reload();
	await page.getByRole('button', { name: /^Browser regression model/ }).click();
	await expect(page.getByRole('button', { name: 'Restore browser draft' })).toHaveCount(0);
});

test('10k by 30 query results render one page with exact amounts and bounded interaction latency', async ({
	page,
}, info) => {
	await login(page);
	await page.goto('/metrics');
	await page.getByRole('button', { name: /^Browser regression model/ }).click();
	await page.getByRole('button', { name: 'Query and evidence' }).click();
	await page.getByRole('checkbox').first().check();
	await page.getByLabel(/All time/).check();
	const exact = '12345678901234567890.12345678901234567890';
	await page.route(`**/api/metrics/models/${payload.model_id}/execute`, (route) =>
		route.fulfill({
			json: {
				data: {
					trusted: true,
					revision: 1,
					row_count: 10000,
					columns: Array.from({ length: 30 }, (_, i) => `column_${i}`),
					rows: Array.from({ length: 10000 }, (_, i) => [
						String(i),
						exact,
						...Array.from({ length: 28 }, () => '真实长度测试数据'.repeat(4)),
					]),
					plan: {
						datasource_id: 'fixture',
						source_ids: ['orders'],
						sql: 'SELECT fixture',
						params: {},
						evidence: {},
						model_fingerprint: 'fixture',
						metric_fingerprints: {},
						request_fingerprint: 'fixture',
					},
				},
			},
		}),
	);
	await page.evaluate(() => {
		const durations: number[] = [];
		(window as unknown as { __longTasks: number[] }).__longTasks = durations;
		new PerformanceObserver((list) =>
			list.getEntries().forEach((entry) => durations.push(entry.duration)),
		).observe({ type: 'longtask', buffered: false });
	});
	const started = Date.now();
	await page.getByRole('button', { name: 'Execute certified query' }).click();
	await expect(page.locator('tbody tr')).toHaveCount(10);
	await expect(page.locator('td')).toHaveCount(300);
	const firstInteractiveMs = Date.now() - started;
	const next = page.getByRole('button', { name: /Next page/i });
	const latencies: number[] = [];
	for (let i = 1; i <= 10; i++) {
		const began = Date.now();
		await next.click();
		await expect(page.locator('tbody td').first()).toHaveText(String(i * 10));
		latencies.push(Date.now() - began);
	}
	await expect(page.getByText(exact, { exact: true })).toHaveCount(10);
	const longTasks = await page.evaluate(
		() => (window as unknown as { __longTasks: number[] }).__longTasks,
	);
	const p95 = [...latencies].sort((a, b) => a - b)[Math.ceil(latencies.length * 0.95) - 1];
	// Includes browser automation + rendering overhead on the CI fixture host.
	expect(firstInteractiveMs).toBeLessThan(8000);
	expect(p95).toBeLessThan(1000);
	await info.attach('table-performance', {
		body: JSON.stringify({
			firstInteractiveMs,
			p95,
			longTasks,
			rows: 10000,
			columns: 30,
			renderedCells: 300,
		}),
		contentType: 'application/json',
	});
});

test('connection wizard keeps keyboard focus and returns it to its trigger', async ({ page }) => {
	await login(page);
	await page.goto('/settings/connections');
	const trigger = page.getByRole('button', { name: 'Create New Connection', exact: true });
	await trigger.focus();
	await page.keyboard.press('Enter');
	const dialog = page.getByRole('dialog', { name: 'Create New Connection', exact: true });
	await expect(dialog).toBeVisible();
	for (let i = 0; i < 30; i++) {
		await page.keyboard.press(i % 2 ? 'Shift+Tab' : 'Tab');
		expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(
			true,
		);
	}
	expect(
		await page.evaluate(() =>
			[...document.body.children]
				.filter(
					(element) =>
						!element.contains(document.querySelector('[role="dialog"]')) &&
						element instanceof HTMLElement,
				)
				.every((element) => (element as HTMLElement).inert),
		),
	).toBe(true);
	await page.keyboard.press('Escape');
	await expect(dialog).toHaveCount(0);
	await expect(trigger).toBeFocused();
});

test('late conversation creation failure cannot restore A into the active B composer', async ({
	page,
}) => {
	const existing = {
		id: 'conversation-b',
		title: 'Existing conversation B',
		created_at: '2026-01-01',
		updated_at: '2026-01-01',
	};
	let finish!: () => void;
	let started!: () => void;
	const seen = new Promise<void>((resolve) => {
		started = resolve;
	});
	const delayed = new Promise<void>((resolve) => {
		finish = resolve;
	});
	await page.route('**/api/conversations', async (route) => {
		if (route.request().method() === 'POST') {
			started();
			await delayed;
			await route.fulfill({ status: 503, json: { error: 'Synthetic failure' } });
		} else await route.fulfill({ json: [existing] });
	});
	await page.route('**/api/conversations/conversation-b', (route) =>
		route.fulfill({ json: { ...existing, messages: [] } }),
	);
	await login(page);
	const composer = page.locator('form textarea');
	await composer.fill('A 的问题');
	await composer.press('Enter');
	await seen;
	await page.getByText('Existing conversation B', { exact: true }).click();
	await expect(page).toHaveURL(/focus=conversation-b/);
	await composer.fill('B 自己的草稿');
	const failed = page.waitForResponse(
		(response) => response.url().endsWith('/api/conversations') && response.status() === 503,
	);
	finish();
	await failed;
	await expect(composer).toHaveValue('B 自己的草稿');
});
