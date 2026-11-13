// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { expect, it, vi } from 'vitest';
import { conversationsApi, type ConversationDetail } from '@/api/conversations';
import { loadCurrentConversation } from './chatHistory';

vi.mock('@/api/conversations', async (original) => ({
	...(await original<typeof import('@/api/conversations')>()),
	conversationsApi: { get: vi.fn() },
}));

it('drops a late history poll after switching threads even when the finished watch was not aborted', async () => {
	let currentRequest = 1;
	let resolve!: (value: ConversationDetail) => void;
	vi.mocked(conversationsApi.get).mockReturnValue(
		new Promise((done) => {
			resolve = done;
		}),
	);
	const pending = loadCurrentConversation('old-thread', () => currentRequest === 1);
	currentRequest = 2;
	resolve({
		id: 'old-thread',
		title: 'Old',
		created_at: '2026-01-01',
		updated_at: '2026-01-01',
		messages: [],
	});
	expect(await pending).toBeNull();
});
