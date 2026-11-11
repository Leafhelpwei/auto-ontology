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
