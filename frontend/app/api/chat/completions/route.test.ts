// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { resolveUser } from '@/auth/resolve-user';
import { userCan } from '@/auth/permissions';
import { POST } from './route';

vi.mock('@/auth/resolve-user', () => ({ resolveUser: vi.fn() }));
vi.mock('@/auth/permissions', () => ({ userCan: vi.fn() }));
vi.mock('@/auth/sso-token', () => ({ resolveSubjectToken: vi.fn(async () => null) }));
vi.mock('@/lib/internalIdentity', () => ({
	buildInternalIdentityHeaders: (id: string) => ({ 'x-trusted-user': id }),
}));
vi.mock('next/server', async (original) => ({
	...(await original<typeof import('next/server')>()),
	after: (callback: () => Promise<void>) => {
		void callback();
	},
}));
const request = (body: Record<string, unknown>) =>
	new Request('http://localhost/api/chat/completions', {
		method: 'POST',
		body: JSON.stringify(body),
		headers: { 'Content-Type': 'application/json', 'x-trusted-user': 'forged' },
	});
beforeEach(() => {
	vi.clearAllMocks();
	vi.mocked(resolveUser).mockResolvedValue({ id: 'real-user', role: 'viewer' });
	vi.mocked(userCan).mockReturnValue(true);
});
afterEach(() => vi.unstubAllGlobals());

it('requires catalog read for metric chat even when chat use is granted', async () => {
	vi.mocked(userCan).mockImplementation((_user, permission) => !('catalog' in permission));
	const fetcher = vi.fn();
	vi.stubGlobal('fetch', fetcher);
	const response = await POST(
		request({
			question: 'amount',
			query_mode: 'metrics',
			metric_context: { model_id: 'sales' },
		}),
	);
	expect(response.status).toBe(403);
	expect(fetcher).not.toHaveBeenCalled();
});

it('rejects missing metric context instead of forwarding an exploratory request', async () => {
	const fetcher = vi.fn();
	vi.stubGlobal('fetch', fetcher);
	const response = await POST(request({ question: 'amount', query_mode: 'metrics' }));
	expect(response.status).toBe(422);
	expect(fetcher).not.toHaveBeenCalled();
});

it('forwards pinned metric context with verified identity and streams unmodified SSE', async () => {
	const fetcher = vi.fn<typeof fetch>(async () => new Response('data: [DONE]\n\n'));
	vi.stubGlobal('fetch', fetcher);
	const payload = {
		question: 'amount',
		conversation_id: 'conversation',
		query_mode: 'metrics',
		metric_context: { model_id: 'sales', revision: 3, selected_metric_ids: [] },
	};
	const response = await POST(request(payload));
	expect(await response.text()).toBe('data: [DONE]\n\n');
	const sent = fetcher.mock.calls[0][1]!;
	expect(JSON.parse(String(sent.body))).toEqual(payload);
	expect(sent.headers).toMatchObject({ 'x-trusted-user': 'real-user' });
	expect(userCan).toHaveBeenCalledWith(expect.anything(), { conversation: ['write'] });
});

it('preserves the original explore path without requiring metric catalog permission', async () => {
	vi.mocked(userCan).mockImplementation((_user, permission) => !('catalog' in permission));
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response('data: [DONE]\n\n')),
	);
	const response = await POST(request({ question: 'existing question', target_db: 'warehouse' }));
	expect(response.status).toBe(200);
	await response.text();
	expect(userCan).not.toHaveBeenCalledWith(expect.anything(), { catalog: ['read'] });
});
