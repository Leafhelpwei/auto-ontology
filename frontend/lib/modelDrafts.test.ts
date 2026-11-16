// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { newModel } from './governedMetrics';
import { readModelDrafts, writeModelDraft, removeModelDraft, type ModelDraft } from './modelDrafts';
beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());
const draft = (): ModelDraft => ({
	format: 1,
	token: 'tab-a',
	updatedAt: Date.now(),
	baseVersion: 3,
	revision: 2,
	model: newModel(),
	advanced: '{unfinished',
	evidence: 'Review in progress',
});
it('scopes recovery to the user, retains raw buffers and original revisions, and fences delayed removal', () => {
	const value = draft();
	expect(writeModelDraft('alice', value)).toBe('');
	expect(readModelDrafts('bob').drafts).toEqual([]);
	expect(readModelDrafts('alice').drafts).toEqual([value]);
	writeModelDraft('alice', { ...value, token: 'tab-b' });
	removeModelDraft('alice', value.model.model_id, 'tab-a');
	expect(readModelDrafts('alice').drafts[0].token).toBe('tab-b');
	removeModelDraft('alice', value.model.model_id, 'tab-b');
	expect(readModelDrafts('alice').drafts).toEqual([]);
});
it('rejects expired or malformed recovery and makes storage failures explicit', () => {
	const value = draft();
	writeModelDraft('alice', { ...value, updatedAt: Date.now() - 8 * 86400000 });
	expect(readModelDrafts('alice').drafts).toEqual([]);
	writeModelDraft('alice', value);
	localStorage.setItem(localStorage.key(0)!, '{broken');
	expect(readModelDrafts('alice').drafts).toEqual([]);
	vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
		throw new DOMException('Quota');
	});
	expect(writeModelDraft('alice', value)).toContain('could not be saved');
});
