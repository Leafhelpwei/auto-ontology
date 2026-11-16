// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { PredicateEditor } from './MetricFields';
import { newSource, predicateSchema } from '@/lib/governedMetrics';
import type { Predicate } from '@/types/governedMetrics';
afterEach(cleanup);
it('selects buyer/seller role paths and preserves the choice through the request validator', () => {
	const updated = vi.fn();
	const facts = newSource(1),
		customers = newSource(2);
	customers.fields = [
		{
			field_id: 'name',
			column_name: 'name',
			catalog_column_id: 'name-id',
			data_type: 'string',
			nullable: false,
		},
	];
	facts.dimensions = ['buyer', 'seller'].map((role) => ({
		dimension_id: role,
		field: { source_id: customers.source_id, field_id: 'name' },
		relationship_path: [role],
	}));
	const Harness = () => {
		const [value, setValue] = useState<Predicate[]>([
			{
				field: { source_id: customers.source_id, field_id: 'name' },
				operator: 'eq',
				value: 'Alice',
			},
		]);
		return (
			<PredicateEditor
				label="Filters"
				sources={[facts, customers]}
				value={value}
				allowRolePaths
				onChange={(next) => {
					setValue(next);
					updated(next);
				}}
			/>
		);
	};
	render(<Harness />);
	expect(screen.getByRole('option', { name: 'buyer (source_1)' })).toBeTruthy();
	fireEvent.change(screen.getByLabelText('Filter role'), { target: { value: '["seller"]' } });
	const value = updated.mock.calls.at(-1)![0][0];
	expect(predicateSchema.parse(value).relationship_path).toEqual(['seller']);
	fireEvent.change(screen.getByLabelText('Filter role'), { target: { value: '__auto' } });
	expect(
		predicateSchema.parse(updated.mock.calls.at(-1)![0][0]).relationship_path,
	).toBeUndefined();
});
