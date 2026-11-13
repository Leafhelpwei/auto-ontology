// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import type { MetricsQueryContext } from '@/types/chat';
import { MetricAnswerDetails } from './MetricAnswerDetails';

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
