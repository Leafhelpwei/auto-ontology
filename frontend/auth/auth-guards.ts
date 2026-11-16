// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { headers } from 'next/headers';
import { redirect } from 'next/navigation';
import { auth } from '@/auth/auth';
import { getPrisma } from '@/lib/prisma';
import { Role } from '@/enums/auth';

/** Authoritative session lookup for server components and route handlers. */
export const getCurrentSession = async () => {
	const session = await auth.api.getSession({ headers: await headers() });
	if (!session) return null;
	const local = await getPrisma().user.findUnique({
		where: { id: session.user.id },
		select: { role: true, banned: true },
	});
	if (!local || local.banned) return null;
	return { ...session, user: { ...session.user, role: local.role } };
};

/** Require any authenticated user; redirect to /login otherwise. */
export const requireUser = async () => {
	const session = await getCurrentSession();
	if (!session) redirect('/login');
	return session;
};

/**
 * Require an admin. This is the real authorization gate — proxy.ts only does an
 * optimistic cookie-presence check and cannot read the role.
 */
export const requireAdmin = async () => {
	const session = await requireUser();
	if (session.user.role !== Role.Admin) redirect('/chat');
	return session;
};
