// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom

import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { MetricsQueryContext } from '@/types/chat';
import { chatQueryContextSchema } from '@/lib/openapiSchemas';
import { MetricAnswerDetails } from './MetricAnswerDetails';
import { metricRequestContext, selectionFromContext } from '@/lib/chatMetricContext';

afterEach(cleanup);

const context: MetricsQueryContext = {
	version: 1,
	query_mode: 'metrics',
	model_id: 'sales',
	revision: 7,
	selected_metric_ids: ['amount'],
	trusted: true,
	status: 'ready',
	columns: ['exact_amount'],
	column_metadata: [
		{ name: 'exact_amount', postgres_type_oid: 1700, json_encoding: 'decimal-string' },
	],
};

it('shows exact decimal results and exposes pinned model provenance', () => {
	const html = renderToStaticMarkup(
		<MetricAnswerDetails
			context={context}
			sqlResponse='[{"exact_amount":"1.000000000000000000000001"}]'
		/>,
	);
	expect(html).toContain('1.000000000000000000000001');
	expect(html).toContain('Certified metric execution');
	expect(html).toContain('revision 7');
});

it('shows column headers for an executed empty table', () => {
	const html = renderToStaticMarkup(<MetricAnswerDetails context={context} sqlResponse="[]" />);
	expect(html).toContain('exact_amount');
	expect(html).toContain('0 rows matched');
});

it('does not label clarification or rejected requests as executed', () => {
	for (const status of ['clarification', 'rejected'] as const) {
		const html = renderToStaticMarkup(
			<MetricAnswerDetails context={{ ...context, status, trusted: false }} />,
		);
		expect(html).toContain('no query executed');
		expect(html).not.toContain('Certified metric execution');
	}
});

it('preserves MySQL metadata and exact values through the public history schema', () => {
	const mysql: MetricsQueryContext = {
		...context,
		column_metadata: [
			{
				name: 'exact_amount',
				dialect: 'mysql',
				postgres_type_oid: null,
				mysql_type_code: 246,
				json_encoding: 'decimal-string',
			},
		],
	};
	const parsed = chatQueryContextSchema.parse(mysql);
	expect(parsed).toEqual(mysql);
	const html = renderToStaticMarkup(
		<MetricAnswerDetails
			context={mysql}
			sqlResponse='[{"exact_amount":"9007199254740993.00000001"}]'
		/>,
	);
	expect(html).toContain('9007199254740993.00000001');
	expect(html).toContain('MySQL type 246');
	expect(html).not.toContain('PostgreSQL OID');
	const empty = renderToStaticMarkup(<MetricAnswerDetails context={mysql} sqlResponse="[]" />);
	expect(empty).toContain('exact_amount');
	expect(empty).toContain('MySQL type 246');
	expect(empty).toContain('0 rows matched');
});

it('retains older PostgreSQL history with no dialect tag', () => {
	expect(chatQueryContextSchema.parse(context)).toEqual(context);
	const html = renderToStaticMarkup(<MetricAnswerDetails context={context} sqlResponse="[]" />);
	expect(html).toContain('PostgreSQL OID 1700');
});

it('round trips the pending scope and sends only its exact confirmation reference', () => {
	const pending: MetricsQueryContext = {
		...context,
		status: 'confirmation_required',
		trusted: false,
		intent_confirmed: false,
		confirmation_fingerprint: 'a'.repeat(64),
		metric_names: { amount: 'Confirmed amount' },
		pending_query: {
			metric_ids: ['amount'],
			dimensions: ['currency'],
			filters: [
				{
					field: { source_id: 'customers', field_id: 'name' },
					operator: 'ne',
					value: 'Alice',
					relationship_path: ['buyer'],
				},
			],
			all_time: true,
			time: null,
			order_by: [],
			limit: 100,
		},
	};
	expect(chatQueryContextSchema.parse(pending)).toEqual(pending);
	const onConfirm = vi.fn();
	render(<MetricAnswerDetails context={pending} onConfirmQuery={onConfirm} />);
	expect(screen.getByText('All history')).toBeTruthy();
	expect(screen.getByText('Confirmed amount (amount)')).toBeTruthy();
	expect(screen.getByText(/customers.name.*buyer.*ne.*Alice/)).toBeTruthy();
	fireEvent.click(screen.getByRole('button', { name: 'Confirm and execute' }));
	expect(onConfirm).toHaveBeenCalledWith(pending);
	const selection = selectionFromContext(pending);
	expect(metricRequestContext(selection)).not.toHaveProperty('confirm_query_fingerprint');
	expect(
		metricRequestContext({
			...selection,
			metric_context: {
				model_id: pending.model_id,
				revision: 7,
				confirm_query_fingerprint: pending.confirmation_fingerprint,
			},
		}),
	).toEqual({
		model_id: pending.model_id,
		revision: 7,
		confirm_query_fingerprint: 'a'.repeat(64),
	});
});

it('keeps historical proposals non-executable without a current confirmation callback', () => {
	const pending: MetricsQueryContext = {
		...context,
		status: 'confirmation_required',
		trusted: false,
		pending_query: {
			metric_ids: ['amount'],
			dimensions: [],
			filters: [],
			time: null,
			all_time: true,
			order_by: [],
			limit: 100,
		},
	};
	render(<MetricAnswerDetails context={pending} />);
	expect(
		(screen.getByRole('button', { name: 'Confirm and execute' }) as HTMLButtonElement).disabled,
	).toBe(true);
	expect(screen.queryByText('Certified metric execution')).toBeNull();
});
