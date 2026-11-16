// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from 'jose';
import { verifyBearer } from './bearer';
const state = vi.hoisted(() => ({ provider: vi.fn(), keys: undefined as unknown }));
vi.mock('@/lib/prisma', () => ({
	getPrisma: () => ({ ssoProvider: { findFirst: state.provider } }),
}));
vi.mock('jose', async (original) => ({
	...(await original<typeof import('jose')>()),
	createRemoteJWKSet: () => state.keys,
}));
let key: CryptoKey;
beforeAll(async () => {
	const pair = await generateKeyPair('RS256');
	key = pair.privateKey;
	const jwk = await exportJWK(pair.publicKey);
	state.keys = createLocalJWKSet({ keys: [{ ...jwk, kid: 'fixture', alg: 'RS256' }] });
});
beforeEach(() => {
	vi.unstubAllEnvs();
	state.provider.mockResolvedValue({
		providerId: 'enterprise',
		issuer: 'https://issuer.example',
		oidcConfig: JSON.stringify({
			jwksEndpoint: 'https://issuer.example/jwks',
			clientId: 'ontology',
		}),
	});
});
async function token(overrides: Record<string, unknown> = {}, typ = 'JWT') {
	const now = Math.floor(Date.now() / 1000);
	const payload = {
		iss: 'https://issuer.example',
		aud: 'ontology',
		sub: 'user-subject',
		iat: now,
		exp: now + 300,
		...overrides,
	};
	return new SignJWT(payload).setProtectedHeader({ alg: 'RS256', typ, kid: 'fixture' }).sign(key);
}
async function check(overrides: Record<string, unknown> = {}, typ = 'JWT') {
	return verifyBearer(new Headers({ authorization: `Bearer ${await token(overrides, typ)}` }));
}
it('verifies a real signed token and returns only a provider-bound subject', async () => {
	expect(await check({ email: 'admin@example.com' })).toEqual({
		subject: 'user-subject',
		issuer: 'https://issuer.example',
		providerId: 'enterprise',
	});
});
it.each([
	{ aud: 'unrelated-client' },
	{ iss: 'https://other.example' },
	{ exp: undefined },
	{ iat: undefined },
	{ sub: undefined },
	{ sub: '' },
	{ exp: 1 },
	{ iat: Math.floor(Date.now() / 1000) + 3600 },
	{ nbf: Math.floor(Date.now() / 1000) + 3600 },
])('rejects invalid claims %j with real signature verification', async (claims) => {
	expect(await check(claims)).toBeNull();
});
it('accepts an explicitly configured cross-service audience', async () => {
	vi.stubEnv('AUTO_ONTOLOGY_SSO_AUDIENCES', '["ai-q"]');
	expect(await check({ aud: 'ai-q' })).not.toBeNull();
	expect(await check({ aud: 'other-service' })).toBeNull();
});
it('rejects malformed allowlists, untrusted transport and token types', async () => {
	vi.stubEnv('AUTO_ONTOLOGY_SSO_AUDIENCES', '{"all":true}');
	expect(await check()).toBeNull();
	vi.stubEnv('AUTO_ONTOLOGY_SSO_AUDIENCES', '[]');
	expect(await check({}, 'refresh+jwt')).toBeNull();
	state.provider.mockResolvedValue({
		providerId: 'enterprise',
		issuer: 'https://issuer.example',
		oidcConfig: JSON.stringify({
			jwksEndpoint: 'http://issuer.example/jwks',
			clientId: 'ontology',
		}),
	});
	expect(await check()).toBeNull();
});
it('rejects a token signed by another key', async () => {
	const other = await generateKeyPair('RS256');
	const now = Math.floor(Date.now() / 1000);
	const forged = await new SignJWT({
		iss: 'https://issuer.example',
		aud: 'ontology',
		sub: 'user-subject',
		iat: now,
		exp: now + 300,
	})
		.setProtectedHeader({ alg: 'RS256', kid: 'fixture' })
		.sign(other.privateKey);
	expect(await verifyBearer(new Headers({ authorization: `Bearer ${forged}` }))).toBeNull();
});
