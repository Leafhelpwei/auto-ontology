// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useQuery } from '@tanstack/react-query';
import { governedMetricsApi } from '@/api/governedMetrics';
import type { ChatSelection } from '@/types/chat';

export const useMetricChatModels = (selection: ChatSelection, canRead: boolean) => {
	const enabled = selection.query_mode === 'metrics' && canRead;
	const modelId = selection.metric_context?.model_id;
	const config = useQuery({
		queryKey: ['governed-metrics', 'config'],
		enabled,
		queryFn: async () => {
			const response = await governedMetricsApi.config();
			if (response.error)
				throw new Error(response.message ?? 'Could not load metric configuration.');
			return response.data;
		},
	});
	const models = useQuery({
		queryKey: ['governed-metrics', 'models'],
		enabled,
		queryFn: async () => {
			const response = await governedMetricsApi.list();
			if (response.error)
				throw new Error(response.message ?? 'Could not load metric models.');
			return response.data;
		},
	});
	const detail = useQuery({
		queryKey: ['governed-metrics', 'model', modelId],
		enabled: enabled && Boolean(modelId),
		queryFn: async () => {
			const response = await governedMetricsApi.get(modelId!);
			if (response.error)
				throw new Error(response.message ?? 'Could not load the selected metric model.');
			return response.data;
		},
	});
	const activeModels =
		models.data?.filter((model) => !model.archived && model.active_revision != null) ?? [];
	const revisionNumber = selection.metric_context?.revision ?? detail.data?.active_revision;
	const revision = detail.data?.revisions.find((item) => item.revision === revisionNumber);
	const error = config.error ?? models.error ?? detail.error;
	const loading =
		enabled && (config.isPending || models.isPending || Boolean(modelId && detail.isPending));
	const staleRevision = Boolean(detail.data && revisionNumber !== detail.data.active_revision);
	const invalidMetricIds =
		selection.metric_context?.selected_metric_ids?.filter(
			(id) => !revision?.payload.metrics.some((metric) => metric.metric_id === id),
		) ?? [];
	let unavailable: string | null = null;
	if (!canRead) unavailable = 'Catalog read permission is required for standard metrics.';
	else if (error) unavailable = `Metric models could not be loaded: ${error.message}`;
	else if (loading) unavailable = 'Loading metric models…';
	else if (config.data?.enabled !== true)
		unavailable = 'Standard metric queries are disabled for this deployment.';
	else if (!modelId) unavailable = 'Select an activated metric model before sending.';
	else if (staleRevision)
		unavailable =
			'This conversation is pinned to a revision that is no longer active. Explicitly select the current active revision to start a new metric context.';
	else if (!revision || revision.status !== 'certified' || detail.data?.archived)
		unavailable = 'The selected model does not have an available certified active revision.';
	else if (invalidMetricIds.length)
		unavailable = `The selected revision does not contain: ${invalidMetricIds.join(', ')}. Choose its metric definitions explicitly.`;
	return {
		config,
		models,
		detail,
		activeModels,
		revision,
		loading,
		error,
		unavailable,
		staleRevision,
	};
};
