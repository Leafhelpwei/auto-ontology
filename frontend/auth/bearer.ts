// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

// Cross-service JWTs require a trusted issuer, explicit audiences, and a
// provider/subject account binding. Email is never an authorization key.
import { createRemoteJWKSet, jwtVerify } from 'jose';
import { getPrisma } from '@/lib/prisma';

export type BearerPrincipal = {
	subject: string;
	providerId: string;
	issuer: string;
};
const cache = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

export async function verifyBearer(headers: Headers): Promise<BearerPrincipal | null> {
	const match = /^Bearer ([^\s]+)$/i.exec(headers.get('authorization') ?? '');
	if (!match) return null;
	const provider = await getPrisma().ssoProvider.findFirst();
	if (!provider?.oidcConfig || !provider.providerId || !provider.issuer) return null;
	try {
		const config: Record<string, unknown> = JSON.parse(provider.oidcConfig);
		const jwksUrl = config.jwksEndpoint ?? config.jwksUrl ?? config.jwks_uri;
		if (typeof jwksUrl !== 'string' || new URL(jwksUrl).protocol !== 'https:') return null;
		const clientId = config.clientId ?? config.client_id;
		const configured: unknown = JSON.parse(process.env.AUTO_ONTOLOGY_SSO_AUDIENCES ?? '[]');
		if (
			!Array.isArray(configured) ||
			!configured.every((item) => typeof item === 'string' && item.length > 0)
		)
			return null;
		const audiences = [
			...configured,
			...(typeof clientId === 'string' && clientId ? [clientId] : []),
		];
		if (!audiences.length) return null;
		const key = `${provider.providerId}|${provider.issuer}|${jwksUrl}`;
		let jwks = cache.get(key);
		if (!jwks) {
			jwks = createRemoteJWKSet(new URL(jwksUrl));
			if (cache.size >= 16) cache.clear();
			cache.set(key, jwks);
		}
		const { payload, protectedHeader } = await jwtVerify(match[1], jwks, {
			issuer: provider.issuer,
			algorithms: ['RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'ES512', 'EdDSA'],
			audience: audiences,
			requiredClaims: ['sub', 'iss', 'aud', 'exp', 'iat'],
			clockTolerance: 30,
		});
		if (protectedHeader.typ && !['JWT', 'at+jwt'].includes(protectedHeader.typ)) return null;
		const now = Math.floor(Date.now() / 1000);
		if (
			typeof payload.sub !== 'string' ||
			!payload.sub ||
			typeof payload.iat !== 'number' ||
			typeof payload.exp !== 'number' ||
			payload.iat > now + 30 ||
			payload.exp <= payload.iat
		)
			return null;
		return { subject: payload.sub, providerId: provider.providerId, issuer: provider.issuer };
	} catch {
		// Do not decode/log rejected claims or provider error bodies.
		console.warn('[bearer] token rejected');
		return null;
	}
}
