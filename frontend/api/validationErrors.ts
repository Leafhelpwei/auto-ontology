// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type FieldError = { path: string; message: string };
/** Keep FastAPI locations and messages; never echo its potentially sensitive `input`. */
export const validationErrors = (details: unknown): FieldError[] => {
	if (!Array.isArray(details)) return [];
	return details.flatMap((entry: unknown) => {
		if (
			!entry ||
			typeof entry !== 'object' ||
			!('loc' in entry) ||
			!('msg' in entry) ||
			!Array.isArray(entry.loc) ||
			typeof entry.msg !== 'string'
		)
			return [];
		if (
			!entry.loc.every(
				(part: unknown) => typeof part === 'string' || typeof part === 'number',
			)
		)
			return [];
		return [
			{
				path: entry.loc
					.filter(
						(part, index) =>
							!(index === 0 && ['body', 'query', 'path'].includes(String(part))),
					)
					.join('.'),
				message: entry.msg,
			},
		];
	});
};
