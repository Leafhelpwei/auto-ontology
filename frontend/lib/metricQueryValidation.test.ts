// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { metricQuerySchema, queryFieldErrors } from './metricQueryValidation';
import type { MetricQueryRequest } from '@/types/governedMetrics';
const valid: MetricQueryRequest = {
	metric_ids: ['revenue'],
	dimensions: [],
	filters: [],
	time: null,
	all_time: true,
	order_by: [],
	limit: 1,
};
it('rejects invalid boundaries while preserving exact decimal filters and submillisecond intervals', () => {
	for (const limit of [0, 1.5, 10001, NaN, Infinity])
		expect(queryFieldErrors({ ...valid, limit }).some((issue) => issue.path === 'limit')).toBe(
			true,
		);
	for (const limit of [1, 10000]) expect(queryFieldErrors({ ...valid, limit })).toEqual([]);
	const time = {
		start: '2026-01-01T00:00:00.000001Z',
		end_exclusive: '2026-01-01T00:00:00.000002Z',
		timezone: 'UTC',
		time_role: 'created',
		granularity: null,
	};
	expect(queryFieldErrors({ ...valid, all_time: false, time })).toEqual([]);
	for (const start of [
		'not a date',
		'2026-02-30T00:00:00Z',
		'2026-01-01T00:00:00',
		time.end_exclusive,
	])
		expect(
			queryFieldErrors({ ...valid, all_time: false, time: { ...time, start } }).length,
		).toBeGreaterThan(0);
	expect(
		queryFieldErrors({
			...valid,
			all_time: false,
			time: { ...time, timezone: 'Imaginary/City' },
		})[0].path,
	).toBe('time.timezone');
	const exact = {
		...valid,
		filters: [
			{
				field: { source_id: 's', field_id: 'revenue' },
				operator: 'eq' as const,
				value: '12345678901234567890.0123456789',
			},
		],
	};
	expect(metricQuerySchema.parse(exact).filters[0].value).toBe(exact.filters[0].value);
});
it('detects request field, enum, range and timestamp contract drift against generated OpenAPI', () => {
	const {
		components: { schemas },
	} = JSON.parse(
		readFileSync(new URL('../../docs/openapi/backend.json', import.meta.url), 'utf8'),
	);
	const query = schemas.MetricQueryRequest;
	expect(Object.keys(metricQuerySchema.shape).sort()).toEqual(
		Object.keys(query.properties).sort(),
	);
	expect(query.additionalProperties).toBe(false);
	expect(query.properties.limit).toMatchObject({ type: 'integer', minimum: 1, maximum: 10000 });
	const time = metricQuerySchema.shape.time.unwrap();
	expect(Object.keys(time.shape).sort()).toEqual(
		Object.keys(schemas.TimeRange.properties).sort(),
	);
	expect(time.shape.granularity.unwrap().options).toEqual(
		schemas.TimeRange.properties.granularity.anyOf[0].enum,
	);
	for (const name of ['start', 'end_exclusive'])
		expect(schemas.TimeRange.properties[name]).toMatchObject({
			type: 'string',
			format: 'date-time',
		});
});
