// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { requests } from './requests';
import type {
	GovernedModel,
	MetricQueryRequest,
	MetricQueryResult,
	ModelDetail,
	ModelSummary,
	InterpretationResult,
} from '@/types/governedMetrics';

const modelPath = (id: string) => `metrics/models/${encodeURIComponent(id)}`;
export const governedMetricsApi = {
	config: () => requests.get<{ data: { enabled: boolean } }>('metrics/config'),
	list: () => requests.get<{ data: ModelSummary[] }>('metrics/models'),
	get: (id: string) => requests.get<{ data: ModelDetail }>(modelPath(id)),
	create: (model: GovernedModel) =>
		requests.post<{ data: ModelDetail }>('metrics/models', { model }),
	save: (model: GovernedModel, version: number) =>
		requests.put<{ data: ModelDetail }>(`${modelPath(model.model_id)}/draft`, {
			model,
			expected_version: version,
		}),
	transition: (
		id: string,
		action: 'validate' | 'certify' | 'activate' | 'deprecate',
		revision: number,
		version: number,
		evidence?: string,
	) =>
		requests.post<{ data: ModelDetail }>(`${modelPath(id)}/${action}`, {
			revision,
			expected_version: version,
			...(evidence ? { evidence } : {}),
		}),
	export: (id: string, revision: number) =>
		requests.get<{ data: unknown }>(`${modelPath(id)}/export`, { revision }),
	import: (model: GovernedModel) =>
		requests.post<{ data: ModelDetail }>('metrics/import', { model }),
	query: (
		id: string,
		action: 'compile' | 'execute',
		query: MetricQueryRequest,
		revision: number,
		preview: boolean,
	) =>
		requests.post<{ data: MetricQueryResult }>(`${modelPath(id)}/${action}`, {
			query,
			revision,
			preview,
		}),
	interpret: (id: string, question: string, selected_metric_ids: string[], revision: number) =>
		requests.post<{ data: InterpretationResult }>(`${modelPath(id)}/interpret`, {
			question,
			selected_metric_ids,
			revision,
		}),
};
