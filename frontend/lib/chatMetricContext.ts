// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type {
	ChatMessage,
	ChatQueryContext,
	ChatSelection,
	MetricsQueryContext,
	MetricChatContext,
} from '@/types/chat';
import type { ParsedTable } from '@/lib/parseSqlResponse';

export const exploreSelection = (): ChatSelection => ({
	query_mode: 'explore',
	metric_context: null,
});

/** A removed or stale model stays selected. Restoring history never chooses another model. */
export const selectionFromContext = (context?: ChatQueryContext | null): ChatSelection =>
	context?.query_mode === 'metrics'
		? {
				query_mode: 'metrics',
				metric_context: {
					model_id: context.model_id,
					...(context.revision != null ? { revision: context.revision } : {}),
					selected_metric_ids: context.selected_metric_ids ?? [],
				},
			}
		: exploreSelection();

export const selectionFromMessages = (messages: ChatMessage[]): ChatSelection => {
	// An untyped/manual/legacy message is a context boundary, not permission
	// to revive an older metric turn. New metric display bubbles carry context.
	return selectionFromContext(messages[messages.length - 1]?.queryContext);
};

export const contextFromSelection = (selection: ChatSelection): ChatQueryContext => {
	if (selection.query_mode === 'metrics') {
		if (!selection.metric_context?.model_id)
			throw new Error('Select an activated metric model before sending.');
		return {
			version: 1,
			query_mode: 'metrics',
			...selection.metric_context,
			selected_metric_ids: selection.metric_context.selected_metric_ids ?? [],
		};
	}
	return { version: 1, query_mode: 'explore' };
};

/** Exact result cells and empty column metadata survive both SSE and persisted history. */
export const metricResultTable = (
	response: string | undefined,
	context: MetricsQueryContext,
): ParsedTable | null => {
	if (response == null) return null;
	let records: unknown;
	try {
		records = JSON.parse(response);
	} catch {
		return null;
	}
	if (
		!Array.isArray(records) ||
		!records.every((row) => row != null && typeof row === 'object' && !Array.isArray(row))
	)
		return null;
	const columns =
		context.columns ??
		context.column_metadata?.map((column) => column.name) ??
		(records[0] ? Object.keys(records[0]) : []);
	return {
		columns,
		rows: records.map((record: Record<string, unknown>) =>
			Object.fromEntries(
				columns.map((column) => {
					const value = record[column];
					return [
						column,
						value == null
							? 'NULL'
							: typeof value === 'object'
								? JSON.stringify(value)
								: String(value),
					];
				}),
			),
		),
	};
};

/** Omit unmodified selection to inherit trusted history; [] is an explicit clear. */
export const metricRequestContext = (selection: ChatSelection): MetricChatContext | undefined => {
	const context = selection.metric_context;
	if (selection.query_mode !== 'metrics' || !context) return undefined;
	return {
		model_id: context.model_id,
		revision: context.revision,
		...(selection.metric_selection_explicit
			? { selected_metric_ids: context.selected_metric_ids ?? [] }
			: {}),
	};
};
