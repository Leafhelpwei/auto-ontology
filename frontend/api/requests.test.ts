// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { expect, it, vi } from 'vitest';
import { requests } from './requests';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('axios', () => ({ default: { create: () => ({ get }) } }));

it('preserves revision conflict codes and messages for the draft recovery UI', async () => {
	get.mockRejectedValue({
		message: 'Request failed with status code 409',
		response: {
			status: 409,
			data: {
				detail: {
					code: 'VERSION_CONFLICT',
					message: 'Model changed since this editor loaded.',
					current_version: 7,
				},
			},
		},
	});
	const result = await requests.get('metrics/models/example');
	expect(result).toMatchObject({
		error: true,
		status: 409,
		code: 'VERSION_CONFLICT',
		message: 'Model changed since this editor loaded.',
		details: { current_version: 7 },
	});
});

it('retains plain FastAPI and Next API error messages', async () => {
	for (const data of [{ detail: 'Permission required' }, { error: 'Permission required' }]) {
		get.mockRejectedValue({ message: 'HTTP error', response: { status: 403, data } });
		expect(await requests.get('test')).toMatchObject({
			error: true,
			message: 'Permission required',
		});
	}
});

it('maps FastAPI array locations to useful field messages without reflecting input values', async () => {
	const details = [
		{
			loc: ['body', 'query', 'limit'],
			msg: 'Input should be greater than or equal to 1',
			type: 'greater_than_equal',
			input: 'sensitive-value',
		},
		{ loc: ['body', 'query', 'filters', 0, 'value'], msg: 'Invalid value', input: 'private' },
	];
	get.mockRejectedValue({
		message: 'Request failed with status code 422',
		response: { status: 422, data: { detail: details } },
	});
	const result = await requests.get('test');
	expect(result.fieldErrors).toEqual([
		{ path: 'query.limit', message: details[0].msg },
		{ path: 'query.filters.0.value', message: details[1].msg },
	]);
	expect(result.message).toContain('query.limit: Input should');
	expect(result.message).not.toContain('sensitive-value');
	expect(result.details).toEqual(details);
});
