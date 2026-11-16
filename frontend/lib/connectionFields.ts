// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import type { ConnectionField } from '@/enums/connection';

export function serializeConnectionField(field: ConnectionField, value = ''): string | boolean {
	if (field.boolean) return value === 'true';
	return field.secret ? value : value.trim();
}
