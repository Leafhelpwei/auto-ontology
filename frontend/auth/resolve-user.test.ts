// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { beforeEach, expect, it, vi } from 'vitest';
import { resolveUser } from './resolve-user';
import { withPermission } from './with-auth';
const state = vi.hoisted(() => ({
	session: vi.fn(),
	account: vi.fn(),
	user: vi.fn(),
	bearer: vi.fn(),
	apiShape: vi.fn(),
	api: vi.fn(),
	oauthShape: vi.fn(),
	oauth: vi.fn(),
}));
vi.mock('next/headers', () => ({ headers: async () => new Headers() }));
vi.mock('@/auth/auth-guards', () => ({ getCurrentSession: state.session }));
vi.mock('@/auth/api-token', () => ({ extractApiToken: state.apiShape, verifyApiToken: state.api }));
vi.mock('@/auth/oauth-token', () => ({
	extractOAuthToken: state.oauthShape,
	verifyOAuthToken: state.oauth,
}));
vi.mock('@/auth/bearer', () => ({ verifyBearer: state.bearer }));
vi.mock('@/lib/prisma', () => ({
	getPrisma: () => ({ account: { findFirst: state.account }, user: { findUnique: state.user } }),
}));
beforeEach(() => {
	vi.resetAllMocks();
	state.session.mockResolvedValue(null);
	state.bearer.mockResolvedValue({
		subject: 'subject',
		providerId: 'enterprise',
		issuer: 'https://issuer.example',
		email: 'admin@example.com',
	});
	state.account.mockResolvedValue({
		user: { id: 'local', role: 'admin', banned: false, emailVerified: true },
	});
});
it('requires the exact provider and subject link and never falls back to email', async () => {
	expect(await resolveUser()).toEqual({ id: 'local', role: 'admin' });
	expect(state.account).toHaveBeenCalledWith(
		expect.objectContaining({ where: { accountId: 'subject', providerId: 'enterprise' } }),
	);
	state.account.mockResolvedValue(null);
	expect(await resolveUser()).toBeNull();
	expect(state.user).not.toHaveBeenCalled();
});
it.each([
	null,
	{ id: 'local', role: 'admin', banned: true, emailVerified: true },
	{ id: 'local', role: 'admin', banned: false, emailVerified: false },
])('rejects deleted, banned or unverified linked user %j', async (user) => {
	state.account.mockResolvedValue({ user });
	expect(await resolveUser()).toBeNull();
});
it('rereads cookie user state so bans and role changes take effect', async () => {
	state.session.mockResolvedValue({ user: { id: 'local', role: 'admin' } });
	state.user.mockResolvedValue({ id: 'local', role: 'viewer', banned: false });
	expect(await resolveUser()).toEqual({ id: 'local', role: 'viewer' });
	state.user.mockResolvedValue({ id: 'local', role: 'admin', banned: true });
	expect(await resolveUser()).toBeNull();
	state.user.mockResolvedValue(null);
	expect(await resolveUser()).toBeNull();
});
it.each(['catalog', 'metric', 'chat', 'connection'])(
	'blocks banned SSO at the shared %s API gate and permits unbanned accounts',
	async (resource) => {
		const permission =
			resource === 'metric'
				? { catalog: ['read'] as const }
				: resource === 'chat'
					? { conversation: ['write'] as const }
					: resource === 'connection'
						? { connection: ['read'] as const }
						: { catalog: ['read'] as const };
		const handler = vi.fn(async () => new Response('allowed'));
		const route = withPermission(permission)(handler);
		state.account.mockResolvedValue({
			user: { id: 'local', role: 'admin', banned: true, emailVerified: true },
		});
		expect((await route(new Request(`https://app.example/api/${resource}`))).status).toBe(401);
		expect(handler).not.toHaveBeenCalled();
		state.account.mockResolvedValue({
			user: { id: 'local', role: 'admin', banned: false, emailVerified: true },
		});
		expect((await route(new Request(`https://app.example/api/${resource}`))).status).toBe(200);
	},
);
it('revoked API and OAuth credentials cannot fall through to SSO', async () => {
	state.apiShape.mockReturnValue('token');
	state.api.mockResolvedValue(null);
	expect(await resolveUser()).toBeNull();
	expect(state.bearer).not.toHaveBeenCalled();
	state.apiShape.mockReturnValue(null);
	state.oauthShape.mockReturnValue('opaque');
	state.oauth.mockResolvedValue(null);
	expect(await resolveUser()).toBeNull();
	expect(state.bearer).not.toHaveBeenCalled();
});
