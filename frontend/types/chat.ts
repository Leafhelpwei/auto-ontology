// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type {
	InterpretationResult,
	MetricPlan,
	MetricQueryRequest,
	MetricQueryResult,
} from './governedMetrics';

export type QueryMode = 'explore' | 'metrics';
export type MetricChatContext = {
	model_id: string;
	revision?: number;
	selected_metric_ids?: string[];
};
export type ChatSelection = {
	query_mode: QueryMode;
	metric_context: MetricChatContext | null;
	metric_selection_explicit?: boolean;
};
export type MetricsQueryContext = {
	version: 1;
	query_mode: 'metrics';
	model_id: string;
	turn_id?: string;
	revision?: number | null;
	model_fingerprint?: string | null;
	selected_metric_ids?: string[] | null;
	status?: 'running' | 'ready' | 'clarification' | 'rejected' | 'error' | 'cancelled';
	trusted?: boolean;
	query?: MetricQueryRequest | null;
	base_query?: MetricQueryRequest | null;
	clarification?: {
		code: string;
		message: string;
		options: InterpretationResult['options'];
	} | null;
	plan?: MetricPlan | null;
	columns?: string[];
	column_metadata?: MetricQueryResult['column_metadata'];
};
export type ChatQueryContext =
	| { version: 1; query_mode: 'explore'; turn_id?: string }
	| MetricsQueryContext;

export type ChatMessage = {
	id: string;
	role: 'user' | 'assistant';
	content: string;
	sql?: string;
	sqlResponse?: string;
	thoughts?: string;
	queryContext?: ChatQueryContext | null;
	timestamp: number;
};

export type ChatRequest = {
	question: string;
	/**
	 * Stable thread UUID. When supplied, prior completed turns are loaded and
	 * the new turn — including the chart/table bubble, once ready — is
	 * persisted to it. An unknown UUID creates the conversation. Omitting it
	 * runs the question statelessly, with no chart step or history.
	 */
	conversationId?: string | null;
	/** Scope retrieval/SQL to one connected database when multiple are loaded. */
	target_db?: string | null;
	query_mode?: QueryMode;
	metric_context?: MetricChatContext | null;
};

/**
 * A graph node starting (`start`) or returning (`end`). Both are sent for
 * every node: the backend can only attach a node's `thought` once it has
 * finished, but waiting for that to show the label would leave the UI naming
 * the *previous* node for the whole time this one runs — a 20s reconstruction
 * displayed as "Validating intent". `phase` is absent on older backends, in
 * which case an event is treated as a plain "next step" as before.
 */
export type StepEvent = {
	type: 'step';
	node: string;
	label: string;
	phase?: 'start' | 'end';
	thought?: string | null;
};

/**
 * The query the agent is about to run, sent once it has cleared syntax and
 * intent validation, so the UI can show it while the database works rather
 * than only alongside the final answer. Drafts that validation rejects are
 * never sent, so what arrives here is always what executes. A query that
 * fails at execution and is rebuilt sends a new event; the newest one wins.
 */
export type SqlEvent = {
	type: 'sql';
	node: string;
	sql: string;
};

/** Shape of the executed SQL result, as returned by `sql_response_from_db`. */
export type SqlResult = string | string[] | { [key: string]: string | number | boolean | null }[];

export type ResultEvent = {
	type: 'result';
	answer: {
		response: string;
		sql_code?: string;
		sql_response_from_db?: SqlResult;
		thoughts?: string;
		query_context?: ChatQueryContext | null;
	};
};

export type ErrorEvent = {
	type: 'error';
	message: string;
	query_context?: ChatQueryContext | null;
};

/**
 * Message 2 — the chart or fallback result table, generated and persisted
 * server-side (see `auto_ontology/server/chat/router.py`'s `_pump`) right after the SQL
 * answer, using the same guarantee as Message 1: it exists whether or not a
 * browser tab is still around to ask for it. Omitted entirely when there was
 * no executed result to visualize.
 */
export type ChartsEvent = {
	type: 'charts';
	content: string;
	sql_response?: string | null;
	query_context?: ChatQueryContext | null;
};

export type MetricContextEvent = { type: 'metric_context'; query_context: MetricsQueryContext };
export type ChatStreamEvent =
	| StepEvent
	| SqlEvent
	| ResultEvent
	| ErrorEvent
	| ChartsEvent
	| MetricContextEvent;

export type GraphStep = {
	node: string;
	label: string;
	thought?: string | null;
	status: 'completed' | 'active';
};

export type Conversation = {
	id: string;
	title: string;
	messages: ChatMessage[];
	createdAt: number;
};
