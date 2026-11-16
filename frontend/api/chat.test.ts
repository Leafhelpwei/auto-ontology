// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, expect, it, vi } from 'vitest';
import { streamChat, watchChat } from './chat';
import type { ChatStreamEvent, MetricsQueryContext } from '@/types/chat';

const context: MetricsQueryContext = {
	version: 1,
	query_mode: 'metrics',
	model_id: 'sales',
	revision: 4,
	turn_id: 'turn-1',
	selected_metric_ids: ['amount'],
	status: 'ready',
	trusted: true,
	columns: ['amount'],
};
const events: ChatStreamEvent[] = [
	{ type: 'metric_context', query_context: { ...context, status: 'running', trusted: false } },
	{
		type: 'result',
		answer: {
			response: 'Executed',
			sql_code: 'SELECT amount',
			sql_response_from_db: '[{"amount":"1.000000000000000000001"}]',
			query_context: context,
		},
	},
	{
		type: 'charts',
		content: '',
		sql_response: '[{"amount":"1.000000000000000000001"}]',
		query_context: context,
	},
];
const response = () =>
	new Response(
		events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') + 'data: [DONE]\n\n',
		{ headers: { 'Content-Type': 'text/event-stream' } },
	);
afterEach(() => vi.unstubAllGlobals());

it('carries pin/result/table context and exact values through live SSE and watch replay', async () => {
	const fetcher = vi.fn<typeof fetch>(async () => response());
	vi.stubGlobal('fetch', fetcher);
	for (const replay of [false, true]) {
		const received: ChatStreamEvent[] = [];
		await new Promise<void>((resolve, reject) => {
			const callbacks = {
				onStep: (event: ChatStreamEvent) => received.push(event),
				onMetricContext: (event: ChatStreamEvent) => received.push(event),
				onResult: (event: ChatStreamEvent) => received.push(event),
				onCharts: (event: ChatStreamEvent) => received.push(event),
				onError: (event: { message: string }) => reject(new Error(event.message)),
				onDone: resolve,
			};
			if (replay) watchChat('conversation', callbacks);
			else
				streamChat(
					{
						question: 'amount all time',
						query_mode: 'metrics',
						metric_context: { model_id: 'sales', revision: 4 },
					},
					callbacks,
				);
		});
		expect(received).toEqual(events);
	}
	const body = JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body));
	expect(body.query_mode).toBe('metrics');
	expect(body.metric_context).toEqual({ model_id: 'sales', revision: 4 });
});

it('defaults old callers to explore and preserves an explicit empty metric selection', async () => {
	const calls: Record<string, unknown>[] = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (_url: string, init?: RequestInit) => {
			calls.push(JSON.parse(String(init?.body)));
			return new Response('data: [DONE]\n\n');
		}),
	);
	for (const payload of [
		{ question: 'old question' },
		{
			question: 'new metric',
			query_mode: 'metrics' as const,
			metric_context: { model_id: 'sales', selected_metric_ids: [] },
		},
	])
		await new Promise<void>((resolve) =>
			streamChat(payload, {
				onStep: vi.fn(),
				onResult: vi.fn(),
				onError: vi.fn(),
				onDone: resolve,
			}),
		);
	expect(calls[0]).toMatchObject({ query_mode: 'explore' });
	expect(calls[0]).not.toHaveProperty('metric_context');
	expect(calls[1].metric_context).toEqual({ model_id: 'sales', selected_metric_ids: [] });
});

it('surfaces structured pre-stream rejection and never calls onStart', async () => {
	const start = vi.fn();
	vi.stubGlobal(
		'fetch',
		vi.fn(async () =>
			Response.json(
				{ detail: { code: 'NOT_ACTIVE', message: 'Choose the current active revision.' } },
				{ status: 409 },
			),
		),
	);
	const error = await new Promise<string>((resolve) =>
		streamChat(
			{ question: 'metric', query_mode: 'metrics', metric_context: { model_id: 'sales' } },
			{
				onStart: start,
				onStep: vi.fn(),
				onResult: vi.fn(),
				onError: (event) => resolve(event.message),
			},
		),
	);
	expect(error).toBe('Choose the current active revision.');
	expect(start).not.toHaveBeenCalled();
});

it('forwards an event cursor and closes the stream reader after terminal data', async () => {
	const cancelled = vi.fn();
	const ids: string[] = [];
	const bytes = new TextEncoder().encode(
		'id: run:2\ndata: {"type":"result","answer":{"response":"ok"}}\n\ndata: [DONE]\n\n',
	);
	const fetcher = vi.fn<typeof fetch>(
		async () =>
			new Response(
				new ReadableStream({
					start(controller) {
						controller.enqueue(bytes);
					},
					cancel: cancelled,
				}),
			),
	);
	vi.stubGlobal('fetch', fetcher);
	await new Promise<void>((resolve, reject) =>
		watchChat(
			'conversation',
			{
				onStep: vi.fn(),
				onResult: vi.fn(),
				onEventId: (id) => ids.push(id),
				onError: (event) => reject(new Error(event.message)),
				onDone: resolve,
			},
			'run:1',
		),
	);
	await vi.waitFor(() => expect(cancelled).toHaveBeenCalledOnce());
	expect(fetcher.mock.calls[0][1]?.headers).toEqual({ 'Last-Event-ID': 'run:1' });
	expect(ids).toEqual(['run:2']);
});

it('rejects an oversized incomplete SSE event and cancels its reader', async () => {
	const cancelled = vi.fn();
	vi.stubGlobal(
		'fetch',
		vi.fn(
			async () =>
				new Response(
					new ReadableStream({
						start(controller) {
							controller.enqueue(new TextEncoder().encode('x'.repeat(2_200_001)));
						},
						cancel: cancelled,
					}),
				),
		),
	);
	const message = await new Promise<string>((resolve) =>
		watchChat('conversation', {
			onStep: vi.fn(),
			onResult: vi.fn(),
			onError: (event) => resolve(event.message),
		}),
	);
	expect(message).toContain('interactive result limit');
	expect(cancelled).toHaveBeenCalledOnce();
});
