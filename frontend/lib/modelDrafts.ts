// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { z } from 'zod';
import { parseModel } from './governedMetrics';
import type { GovernedModel } from '@/types/governedMetrics';

const prefix = (userId: string) => `auto-ontology:model-draft:v1:${encodeURIComponent(userId)}:`;
const key = (userId: string, modelId: string) => `${prefix(userId)}${encodeURIComponent(modelId)}`;
const ttl = 7 * 24 * 60 * 60 * 1000;
const schema = z
	.object({
		format: z.literal(1),
		token: z.string(),
		updatedAt: z.number().finite(),
		baseVersion: z.number().int().nonnegative().nullable(),
		revision: z.number().int().nonnegative(),
		model: z.unknown(),
		advanced: z.string().nullable(),
		evidence: z.string(),
	})
	.strict();
export type ModelDraft = Omit<z.infer<typeof schema>, 'model'> & { model: GovernedModel };
export const draftEvent = 'auto-ontology:model-drafts-changed';

/** Recovery stays scoped to the signed-in user and never supplies certification state. */
export const readModelDrafts = (userId: string): { drafts: ModelDraft[]; error: string } => {
	try {
		const drafts: ModelDraft[] = [];
		for (let i = 0; i < localStorage.length; i++) {
			const name = localStorage.key(i);
			if (!name?.startsWith(prefix(userId))) continue;
			try {
				const text = localStorage.getItem(name)!;
				if (text.length > 2_000_000) continue;
				const value = schema.parse(JSON.parse(text));
				if (Date.now() - value.updatedAt > ttl || value.updatedAt > Date.now() + 60_000)
					continue;
				const model = parseModel(JSON.stringify(value.model));
				if (name === key(userId, model.model_id)) drafts.push({ ...value, model });
			} catch {
				/* A malformed record cannot enter the model editor. */
			}
		}
		return { drafts: drafts.sort((a, b) => b.updatedAt - a.updatedAt), error: '' };
	} catch {
		return {
			drafts: [],
			error: 'Browser draft recovery is unavailable. Download edits before leaving this page.',
		};
	}
};
export const writeModelDraft = (userId: string, draft: ModelDraft): string => {
	try {
		const text = JSON.stringify(draft);
		if (text.length > 2_000_000) throw new Error('Draft size');
		localStorage.setItem(key(userId, draft.model.model_id), text);
		window.dispatchEvent(new Event(draftEvent));
		return '';
	} catch {
		return 'Draft recovery could not be saved in this browser. Download edits before leaving this page.';
	}
};
/** A delayed save from another tab must not erase a newer tab's recovery copy. */
export const removeModelDraft = (userId: string, modelId: string, token: string): string => {
	try {
		const text = localStorage.getItem(key(userId, modelId));
		if (text && JSON.parse(text).token === token) {
			localStorage.removeItem(key(userId, modelId));
			window.dispatchEvent(new Event(draftEvent));
		}
		return '';
	} catch {
		return 'The browser recovery copy could not be cleared.';
	}
};
