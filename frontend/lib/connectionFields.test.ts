// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { expect, it } from 'vitest';
import { ConnectionType, connectionFieldsFor } from '@/enums/connection';
import { serializeConnectionField } from './connectionFields';
it.each(Object.values(ConnectionType))(
	'preserves all %s secret bytes and normalizes host whitespace',
	(type) => {
		for (const field of connectionFieldsFor(type)) {
			if (field.secret)
				expect(serializeConnectionField(field, ' \n密码😀\t ')).toBe(' \n密码😀\t ');
			if (field.key === 'host')
				expect(serializeConnectionField(field, ' localhost ')).toBe('localhost');
		}
	},
);
it('keeps whitespace-only secrets distinct from an unchanged empty secret', () => {
	expect(
		serializeConnectionField({ key: 'password', label: 'Password', secret: true }, '  '),
	).toBe('  ');
	expect(serializeConnectionField({ key: 'password', label: 'Password', secret: true }, '')).toBe(
		'',
	);
});
