// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { resolveUser } from '@/auth/resolve-user';
import { userCan } from '@/auth/permissions';
import { findOwnedConversation } from '@/lib/chatConversations';
import { GET } from './route';

vi.mock('@/auth/resolve-user', () => ({ resolveUser: vi.fn() }));
vi.mock('@/auth/permissions', () => ({ userCan: vi.fn() }));
vi.mock('@/lib/chatConversations', () => ({ findOwnedConversation: vi.fn() }));
vi.mock('@/lib/internalIdentity', () => ({
	buildInternalIdentityHeaders: (id: string) => ({ 'x-trusted-user': id }),
}));
beforeEach(() => {
	vi.clearAllMocks();
	vi.mocked(resolveUser).mockResolvedValue({ id: 'real-user', role: 'viewer' });
	vi.mocked(userCan).mockReturnValue(true);
	vi.mocked(findOwnedConversation).mockResolvedValue({ id: 'conversation' });
});
afterEach(() => vi.unstubAllGlobals());

it('forwards cursor and verified ownership without forwarding forged identity', async () => {
	const fetcher = vi.fn<typeof fetch>(async () => new Response('id: run:3\ndata: [DONE]\n\n'));
	vi.stubGlobal('fetch', fetcher);
	const req = new Request('http://localhost/api/chat/watch?conversation_id=conversation', {
		headers: { 'last-event-id': 'run:2', 'x-trusted-user': 'forged' },
	});
	const res = await GET(req);
	expect(await res.text()).toBe('id: run:3\ndata: [DONE]\n\n');
	expect(fetcher.mock.calls[0][1]?.headers).toMatchObject({
		'Last-Event-ID': 'run:2',
		'x-trusted-user': 'real-user',
	});
	expect(fetcher.mock.calls[0][1]?.signal).toBe(req.signal);
});

it('does not contact another replica for an unowned conversation', async () => {
	vi.mocked(findOwnedConversation).mockResolvedValue(null);
	const fetcher = vi.fn();
	vi.stubGlobal('fetch', fetcher);
	const res = await GET(new Request('http://localhost/api/chat/watch?conversation_id=other'));
	expect(res.status).toBe(404);
	expect(fetcher).not.toHaveBeenCalled();
});
