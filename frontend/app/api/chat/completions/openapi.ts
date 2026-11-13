// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { z } from 'zod';
import type { OpenApiRoute } from '@/types/openapi';
import { chatQueryContextSchema } from '@/lib/openapiSchemas';

// The handler forwards the parsed body to FastAPI verbatim, so the shape is the
// backend's `ChatRequest`. It is restated here rather than merged from
// `docs/openapi/backend.json` because this hand-rolled streaming proxy also
// enforces conversation permissions before forwarding the body.
export const openapi: OpenApiRoute = {
	post: {
		body: {
			description:
				'SQL plus a formatted answer, streamed as SSE. When the answer has an ' +
				'executed result, the backend also generates and persists a chart (or ' +
				'fallback table) and streams it back on this same connection as its own ' +
				'`charts` event — no follow-up call needed. Reuse one `conversation_id` for ' +
				'follow-up turns; an unknown UUID creates a conversation, while an ID owned ' +
				'by another user returns 404. Omitting `conversation_id` runs the question ' +
				'statelessly, with no history. `query_mode=explore` preserves exploratory SQL. ' +
				'`query_mode=metrics` requires catalog:read and an explicit activated metric model; it never falls back to exploratory SQL. ' +
				'The SSE sequence uses step/sql/result/charts and [DONE]. Metric runs additionally emit metric_context while pinning the revision. ' +
				'Metric result.answer.query_context and charts.query_context carry model/revision, status, clarification options and execution evidence. ' +
				'Metric charts events contain an exact JSON result table rather than an LLM-generated chart. Decimal and large integer cells are strings. ' +
				'Clarification/rejection is a result without executed SQL. Reuse the same conversation and model/revision for follow-up details; an explicit different model/revision resets metric context.',
			schema: z.object({
				question: z.string().min(1),
				query_mode: z.enum(['explore', 'metrics']).default('explore'),
				metric_context: z
					.object({
						model_id: z.string().min(1),
						revision: z.number().int().positive().optional(),
						selected_metric_ids: z
							.array(z.string())
							.nullish()
							.describe(
								'Omit to inherit the trusted prior selection; [] explicitly clears it; a nonempty list selects variants for this turn.',
							),
					})
					.nullish()
					.describe(
						'Required for metrics mode. Omitting revision retains the previously pinned revision for this model; stale revisions are rejected, not silently replaced.',
					),
				evidence: z.string().nullish(),
				conversation_id: z.string().uuid().nullish(),
				target_db: z.string().nullish(),
				prediction: z.boolean().nullish(),
			}),
		},
		responses: {
			200: {
				description:
					'Server-Sent Events. Metric events preserve the server-authored query context described by the history message schema.',
				contentType: 'text/event-stream',
				schema: z.string().meta({
					'x-metric-query-context-schema': z.toJSONSchema(chatQueryContextSchema),
				}),
			},
			422: {
				description:
					'Invalid request or missing metric model/context. Metrics mode rejects exploratory target_db/prediction/evidence overrides.',
				schema: z.object({ detail: z.unknown() }),
			},
			503: {
				description: 'Governed metric execution is disabled.',
				schema: z.object({ detail: z.unknown() }),
			},
			409: {
				description:
					'Relayed from the backend: a run is already in flight for this ' +
					'conversation, the selected metric revision is no longer active/certified, or (explore mode only) the semantic layer has not been compiled yet.',
				schema: z.object({
					detail: z.union([
						z.string(),
						z.object({ code: z.string(), message: z.string() }).passthrough(),
					]),
				}),
			},
		},
	},
};
