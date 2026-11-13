// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { conversationsApi, toConversation } from '@/api/conversations';

/** Guard both sides of the await: a completed watch can outlive its UI controller. */
export const loadCurrentConversation = async (conversationId: string, isCurrent: () => boolean) => {
	if (!isCurrent()) return null;
	const detail = await conversationsApi.get(conversationId);
	return isCurrent() ? toConversation(detail) : null;
};
