// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { resolveUser } from '@/auth/resolve-user';
import { proxyToBackend } from '@/auth/proxy-backend';
import { POST } from './route';

vi.mock('@/auth/resolve-user', () => ({ resolveUser: vi.fn() }));
vi.mock('@/auth/proxy-backend', () => ({
	proxyToBackend: vi.fn(async () => Response.json({ data: { trusted: true } })),
}));
const request = (preview: unknown) =>
	new Request('http://localhost/api/metrics/models/example/compile', {
		method: 'POST',
		body: JSON.stringify({ preview, query: { metric_ids: ['amount'] } }),
		headers: { 'Content-Type': 'application/json', 'x-auto-ontology-user-id': 'spoofed-user' },
	});

beforeEach(() => {
	vi.clearAllMocks();
});

describe('metric compile permission boundary', () => {
	it('rejects unauthenticated callers before the backend', async () => {
		vi.mocked(resolveUser).mockResolvedValue(null);
		expect((await POST(request(false))).status).toBe(401);
		expect(proxyToBackend).not.toHaveBeenCalled();
	});
	it('permits a viewer to compile certified queries but not preview drafts', async () => {
		vi.mocked(resolveUser).mockResolvedValue({ id: 'reader', role: 'viewer' });
		expect((await POST(request(false))).status).toBe(200);
		expect(proxyToBackend).toHaveBeenCalledWith(expect.any(Request), { userId: 'reader' });
		vi.mocked(proxyToBackend).mockClear();
		expect((await POST(request(true))).status).toBe(403);
		expect(proxyToBackend).not.toHaveBeenCalled();
	});
	it('forwards an editor preview with the resolved identity and intact body', async () => {
		vi.mocked(resolveUser).mockResolvedValue({ id: 'editor', role: 'admin' });
		const original = request(true);
		expect((await POST(original)).status).toBe(200);
		expect(proxyToBackend).toHaveBeenCalledWith(original, { userId: 'editor' });
		expect((await original.json()).preview).toBe(true);
	});
	it('rejects malformed JSON', async () => {
		vi.mocked(resolveUser).mockResolvedValue({ id: 'reader', role: 'viewer' });
		expect(
			(
				await POST(
					new Request('http://localhost/api/metrics/models/example/compile', {
						method: 'POST',
						body: '{',
					}),
				)
			).status,
		).toBe(400);
		expect(proxyToBackend).not.toHaveBeenCalled();
	});
});
