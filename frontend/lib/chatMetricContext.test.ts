// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest';
import { toConversation } from '@/api/conversations';
import type { ChatMessage, MetricsQueryContext } from '@/types/chat';
import {
	contextFromSelection,
	metricRequestContext,
	metricResultTable,
	selectionFromContext,
	selectionFromMessages,
} from './chatMetricContext';

const context: MetricsQueryContext = {
	version: 1,
	query_mode: 'metrics',
	model_id: 'retired-model',
	revision: 3,
	selected_metric_ids: ['confirmed_amount'],
	status: 'ready',
	trusted: true,
	columns: ['amount', 'count'],
	column_metadata: [{ name: 'amount', postgres_type_oid: 1700, json_encoding: 'decimal-string' }],
};
const message = (queryContext?: ChatMessage['queryContext']): ChatMessage => ({
	id: 'm',
	role: 'assistant',
	content: '',
	timestamp: 0,
	queryContext,
});

describe('metric conversation context', () => {
	it('restores exact model, pinned revision and variant without selecting a replacement', () => {
		expect(selectionFromMessages([message(context)])).toMatchObject({
			query_mode: 'metrics',
			metric_context: {
				model_id: 'retired-model',
				revision: 3,
				selected_metric_ids: ['confirmed_amount'],
			},
		});
	});
	it('does not cross a legacy, manual, or exploratory message boundary', () => {
		for (const last of [
			message(),
			message(null),
			message({ version: 1, query_mode: 'explore' }),
		])
			expect(selectionFromMessages([message(context), last]).query_mode).toBe('explore');
	});
	it('distinguishes unmodified inherited selection, explicit clear, and explicit variant', () => {
		const selection = selectionFromContext(context);
		expect(metricRequestContext(selection)).not.toHaveProperty('selected_metric_ids');
		expect(
			metricRequestContext({ ...selection, metric_selection_explicit: true }),
		).toMatchObject({ selected_metric_ids: ['confirmed_amount'] });
		expect(
			metricRequestContext({
				...selection,
				metric_selection_explicit: true,
				metric_context: { model_id: 'retired-model', revision: 3, selected_metric_ids: [] },
			}),
		).toMatchObject({ selected_metric_ids: [] });
	});
	it('refuses metric mode without a model rather than constructing exploration context', () => {
		expect(() => contextFromSelection({ query_mode: 'metrics', metric_context: null })).toThrow(
			'Select an activated metric model',
		);
	});
	it('preserves exact result strings and empty columns identically through history mapping', () => {
		const raw = '[{"amount":"1.000000000000000000000001","count":"9007199254740993"}]';
		const history = toConversation({
			id: 'c',
			title: 'Metrics',
			created_at: '2026-01-01',
			updated_at: '2026-01-01',
			messages: [
				{
					id: 'm',
					conversation_id: 'c',
					role: 'assistant',
					content: '',
					sql_code: null,
					sql_response: raw,
					query_context: context,
					created_at: '2026-01-01',
				},
			],
		});
		expect(metricResultTable(history.messages[0].sqlResponse, context)?.rows[0]).toEqual({
			amount: '1.000000000000000000000001',
			count: '9007199254740993',
		});
		expect(history.messages[0].queryContext).toEqual(context);
		expect(metricResultTable('[]', context)).toEqual({
			columns: ['amount', 'count'],
			rows: [],
		});
	});
});
