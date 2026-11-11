// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { withPermission } from '@/auth/with-auth';
import { proxyToBackend } from '@/auth/proxy-backend';
import { userCan } from '@/auth/permissions';

// A reader can compile certified queries. Draft previews require catalog:edit.
// Parse a clone so the original request body is still available to the proxy.
export const POST = withPermission({ catalog: ['read'] })(async (req, { user }) => {
	let body: unknown;
	try {
		body = await req.clone().json();
	} catch {
		return Response.json({ error: 'Invalid JSON request.' }, { status: 400 });
	}
	if (
		body != null &&
		typeof body === 'object' &&
		'preview' in body &&
		body.preview &&
		!userCan(user, { catalog: ['edit'] })
	) {
		return Response.json(
			{ error: 'Draft previews require catalog editing permission.' },
			{ status: 403 },
		);
	}
	return proxyToBackend(req, { userId: user.id });
});
