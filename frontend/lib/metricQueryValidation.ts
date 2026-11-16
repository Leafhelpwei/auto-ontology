// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { z } from 'zod';
import { predicateSchema } from './governedMetrics';
import type { MetricQueryRequest } from '@/types/governedMetrics';
import type { FieldError } from '@/api/validationErrors';

const timestamp = z.iso.datetime({
	offset: true,
	message: 'Enter a valid ISO timestamp with UTC offset.',
});
const instant = (text: string): bigint => {
	const fraction = /\.(\d+)/.exec(text)?.[1] ?? '';
	return (
		BigInt(Date.parse(text.replace(/\.\d+/, ''))) * BigInt(1_000_000) +
		BigInt(fraction.padEnd(9, '0').slice(0, 9))
	);
};
export const metricQuerySchema = z
	.object({
		metric_ids: z.array(z.string().min(1)).min(1, 'Select at least one metric variant.'),
		dimensions: z.array(z.string()),
		filters: z.array(predicateSchema),
		time: z
			.object({
				start: timestamp,
				end_exclusive: timestamp,
				timezone: z
					.string()
					.min(1)
					.refine((value) => {
						try {
							new Intl.DateTimeFormat('en', { timeZone: value });
							return true;
						} catch {
							return false;
						}
					}, 'Choose a valid IANA timezone.'),
				time_role: z.string().min(1, 'Enter the business time role.'),
				metric_roles: z.record(z.string().min(1), z.string().min(1)).optional(),
				granularity: z.enum(['day', 'month', 'year']).nullable(),
			})
			.strict()
			.nullable(),
		all_time: z.boolean(),
		order_by: z.array(
			z.object({ field_id: z.string(), direction: z.enum(['asc', 'desc']) }).strict(),
		),
		limit: z
			.number()
			.int('Row limit must be an integer.')
			.min(1, 'Row limit must be at least 1.')
			.max(10000, 'Row limit must not exceed 10,000.'),
	})
	.strict()
	.superRefine((query, ctx) => {
		if (!query.all_time && !query.time)
			ctx.addIssue({
				code: 'custom',
				path: ['time'],
				message: 'Choose an explicit time range or All time.',
			});
		if (query.all_time && query.time)
			ctx.addIssue({
				code: 'custom',
				path: ['time'],
				message: 'All time cannot include a time range.',
			});
		if (
			query.time &&
			timestamp.safeParse(query.time.start).success &&
			timestamp.safeParse(query.time.end_exclusive).success &&
			instant(query.time.end_exclusive) <= instant(query.time.start)
		)
			ctx.addIssue({
				code: 'custom',
				path: ['time', 'end_exclusive'],
				message: 'Exclusive end must be after the start.',
			});
	});
export const queryFieldErrors = (query: MetricQueryRequest): FieldError[] => {
	const result = metricQuerySchema.safeParse(query);
	return result.success
		? []
		: result.error.issues.map((issue) => ({
				path: issue.path.join('.'),
				message: issue.message,
			}));
};
