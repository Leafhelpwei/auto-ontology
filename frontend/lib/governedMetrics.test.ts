// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest';
import {
	assertExactJsonNumbers,
	canTransitionRevision,
	parseModel,
	predicateScalar,
	prepareDraftImport,
	splitIds,
} from './governedMetrics';
import example from './examples/ordersMetricModel.json';

const reviewed = {
	status: 'validated',
	dirty: false,
	pendingJson: false,
	busy: false,
	canEdit: true,
	evidence: 'Reviewed against confirmed order totals.',
};

describe('saved revision governance', () => {
	it('cannot certify changed form content, un-applied JSON, or an unreviewed draft', () => {
		expect(canTransitionRevision('certify', reviewed)).toBe(true);
		for (const patch of [
			{ dirty: true },
			{ pendingJson: true },
			{ status: 'draft' },
			{ evidence: '  ' },
			{ canEdit: false },
			{ busy: true },
		]) {
			expect(canTransitionRevision('certify', { ...reviewed, ...patch })).toBe(false);
		}
	});
	it('requires a certified saved revision to activate and never reactivates deprecated revisions', () => {
		expect(canTransitionRevision('activate', reviewed)).toBe(false);
		expect(canTransitionRevision('activate', { ...reviewed, status: 'certified' })).toBe(true);
		expect(
			canTransitionRevision('activate', { ...reviewed, status: 'certified', dirty: true }),
		).toBe(false);
		expect(canTransitionRevision('activate', { ...reviewed, status: 'deprecated' })).toBe(
			false,
		);
	});
});

describe('ordinary model import', () => {
	it('preserves definitions but discards certification provenance and creates a new identity', () => {
		const imported = prepareDraftImport(
			JSON.stringify({
				model: example,
				format: 'auto-ontology-governed-model-v1',
				provenance: {
					status: 'certified',
					active_revision: 19,
					certification: { reviewer_id: 'claimed-owner' },
				},
			}),
		);
		expect(imported.model_id).not.toBe(example.model_id);
		expect(imported.metrics).toEqual(parseModel(JSON.stringify(example)).metrics);
		expect(imported).not.toHaveProperty('provenance');
		expect(imported).not.toHaveProperty('certification');
		expect(imported).not.toHaveProperty('active_revision');
	});
	it('rejects malformed nested structures before the editor renders them', () => {
		expect(() =>
			parseModel(
				JSON.stringify({
					...example,
					sources: [{ ...example.sources[0], fields: 'malformed' }],
				}),
			),
		).toThrow('sources.0.fields');
		expect(() =>
			parseModel(
				JSON.stringify({
					...example,
					metrics: [
						{ ...example.metrics[0], expression: { kind: 'raw_sql', sql: 'SELECT 1' } },
					],
				}),
			),
		).toThrow();
	});
});

describe('exact numeric input', () => {
	it('retains large integers and high precision decimal filters as strings', () => {
		for (const value of ['9007199254740993', '1.000000000000000000000001']) {
			expect(predicateScalar(value, 'number')).toBe(value);
			expect(predicateScalar(value, 'string')).toBe(value);
		}
		expect(predicateScalar('true', 'boolean')).toBe(true);
		expect(predicateScalar('true', 'string')).toBe('true');
	});
	it('rejects lossy raw JSON numbers before JSON.parse rounds them', () => {
		for (const token of ['9007199254740993', '1.000000000000000000000001', '1e999', '1e-999']) {
			expect(() => assertExactJsonNumbers(`{"value":${token}}`)).toThrow('decimal strings');
			expect(() => assertExactJsonNumbers(`{"value":"${token}"}`)).not.toThrow();
		}
		for (const token of ['9007199254740991', '0.125', '1.25e-3', '-0', '100.00']) {
			expect(() => assertExactJsonNumbers(`{"value":${token}}`)).not.toThrow();
		}
	});
	it('preserves a trailing separator while typing a composite grain key', () => {
		expect(splitIds('tenant_id,').join(', ')).toBe('tenant_id, ');
		expect(splitIds('tenant_id, order_id')).toEqual(['tenant_id', 'order_id']);
	});
});
