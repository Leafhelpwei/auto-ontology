// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom

import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ModelEditor } from './ModelEditor';
import { newModel, newSource, newMetric } from '@/lib/governedMetrics';
import type { GovernedModel } from '@/types/governedMetrics';
import { unsafeModelEdit } from '@/lib/modelIntegrity';

afterEach(cleanup);
const Harness = ({ initial }: { initial: GovernedModel }) => {
	const [model, update] = useState(initial);
	return (
		<ModelEditor model={model} onChange={update} disabled={false} onPendingChange={vi.fn()} />
	);
};

it.each([
	['source', 'Sources', 'Source ID', 'Add source', 'Remove source'],
	['field', 'Sources', 'Field ID', 'Add field binding', 'Remove field'],
	['dimension', 'Sources', 'Dimension ID', 'Add dimension', 'Remove dimension'],
	['relationship', 'Relationships', 'Relationship ID', 'Add relationship', 'Remove relationship'],
	['metric', 'Metrics', 'Metric ID', 'Add metric', 'Remove metric'],
])(
	'allocates distinct %s IDs after deleting the middle item',
	(kind, section, label, add, remove) => {
		const model = newModel();
		if (kind === 'source') model.sources = [1, 2, 3].map((index) => newSource(index));
		if (kind === 'field')
			model.sources[0].fields = [1, 2, 3].map((index) => ({
				field_id: `field_${index}`,
				column_name: '',
				catalog_column_id: '',
				data_type: 'string',
				nullable: true,
			}));
		if (kind === 'dimension')
			model.sources[0].dimensions = [1, 2, 3].map((index) => ({
				dimension_id: `dimension_${index}`,
				field: { source_id: 'source_1', field_id: '' },
				relationship_path: [],
			}));
		if (kind === 'relationship')
			model.relationships = [1, 2, 3].map((index) => ({
				relationship_id: `relationship_${index}`,
				source_id: 'source_1',
				target_id: 'source_1',
				role: '',
				keys: [],
				cardinality: 'many_to_one',
				join_type: 'left',
				predicates: [],
			}));
		if (kind === 'metric')
			model.metrics = [1, 2, 3].map((index) => newMetric(index, 'source_1'));
		render(<Harness initial={model} />);
		fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${section} \\(`) }));
		fireEvent.click(screen.getAllByRole('button', { name: remove })[1]);
		fireEvent.click(screen.getByRole('button', { name: add }));
		const ids = screen
			.getAllByLabelText(new RegExp(`^${label}`))
			.map((element) => (element as HTMLInputElement).value);
		expect(ids).toHaveLength(3);
		expect(new Set(ids).size).toBe(3);
	},
);

it('blocks referenced source deletion/rename and shows the dependency', () => {
	render(<Harness initial={newModel()} />);
	fireEvent.click(screen.getByRole('button', { name: /^Sources / }));
	fireEvent.change(screen.getByLabelText('Source ID'), { target: { value: 'renamed' } });
	expect((screen.getByLabelText('Source ID') as HTMLInputElement).value).toBe('source_1');
	expect(screen.getByRole('alert').textContent).toContain(
		'Metric metric_1 references source source_1',
	);
	fireEvent.click(screen.getByRole('button', { name: 'Remove source' }));
	expect(screen.getByLabelText('Source ID')).toBeTruthy();
});

it('permits atomic identity/reference changes and rejects a broken composite metric', () => {
	const before = newModel();
	before.metrics.push({
		...newMetric(2),
		kind: 'composite',
		expression: { kind: 'metric', metric_id: 'metric_1' },
	});
	const renamed = structuredClone(before);
	renamed.sources[0].source_id = 'orders';
	renamed.metrics[0].source_ids = ['orders'];
	expect(unsafeModelEdit(before, renamed)).toEqual([]);
	const broken = { ...before, metrics: before.metrics.slice(1) };
	expect(unsafeModelEdit(before, broken)).toContain(
		'Metric metric_2 expression references metric metric_1',
	);
});
