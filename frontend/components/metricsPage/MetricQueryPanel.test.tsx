// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { newMetric } from '@/lib/governedMetrics';
import type { GovernedModel } from '@/types/governedMetrics';
import { MetricQueryPanel } from './MetricQueryPanel';

const model: GovernedModel = {
	model_id: 'sales',
	name: 'Sales',
	dialect: 'postgres',
	sources: [],
	relationships: [],
	metrics: [
		{
			...newMetric(1),
			metric_id: 'confirmed',
			name: 'Confirmed revenue',
			family_id: 'revenue',
			default_of_family: true,
		},
		{ ...newMetric(2), metric_id: 'paid', name: 'Paid revenue', family_id: 'revenue' },
	],
};

it('requires explicit family variant selection even when one is marked default', () => {
	const html = renderToStaticMarkup(
		<MetricQueryPanel model={model} revision={1} active enabled canEdit hasUnsaved={false} />,
	);
	expect(html).toContain('Confirmed revenue (confirmed)');
	expect(html).toContain('Paid revenue (paid)');
	expect(html).toContain('family default');
	expect(html).not.toContain('checked=""');
	expect(html).toContain('A family name alone does not select a variant');
});

it('makes draft preview and certified execution separate and locks unsaved edits', () => {
	const html = renderToStaticMarkup(
		<MetricQueryPanel model={model} revision={2} active={false} enabled canEdit hasUnsaved />,
	);
	expect(html).toContain('execution is unavailable');
	expect(html).toContain('Save or discard model edits');
	expect(html).toContain('Preview draft SQL');
	expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Execute certified query<\/button>/);
});
