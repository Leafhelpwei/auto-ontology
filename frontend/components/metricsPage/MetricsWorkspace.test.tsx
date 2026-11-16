// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MetricsWorkspace } from './MetricsWorkspace';
import { governedMetricsApi } from '@/api/governedMetrics';
import { modelTemplate } from '@/lib/governedMetrics';
import type { GovernedModel, ModelDetail } from '@/types/governedMetrics';

vi.mock('@/auth/auth-client', () => ({
	useSession: () => ({ data: { user: { id: 'u', role: 'admin' } } }),
}));
vi.mock('@/common/icons', () => ({
	Icon: () => <span aria-hidden="true" />,
	IconName: { ChartBar: 'chart' },
}));
vi.mock('@/api/governedMetrics', () => ({
	governedMetricsApi: {
		config: vi.fn(),
		list: vi.fn(),
		get: vi.fn(),
		transition: vi.fn(),
		save: vi.fn(),
		create: vi.fn(),
	},
}));
vi.mock('./MetricQueryPanel', () => ({ MetricQueryPanel: () => null }));
vi.mock('./ModelEditor', () => ({
	ModelEditor: ({
		model,
		onChange,
		initialAdvanced,
		onPendingChange,
	}: {
		model: GovernedModel;
		onChange: (model: GovernedModel) => void;
		initialAdvanced?: string | null;
		onPendingChange: (pending: boolean, json?: string) => void;
	}) => (
		<>
			<input
				aria-label="Draft name"
				value={model.name}
				onChange={(event) => onChange({ ...model, name: event.target.value })}
			/>
			<textarea
				aria-label="Pending JSON"
				defaultValue={initialAdvanced ?? ''}
				onChange={(event) => onPendingChange(true, event.target.value)}
			/>
		</>
	),
}));

const detail = (id: string, version = 1): ModelDetail => ({
	model_id: id,
	name: id,
	owner_id: null,
	version,
	latest_revision: 1,
	active_revision: null,
	archived: false,
	revisions: [
		{
			revision: 1,
			status: 'draft',
			payload: { ...modelTemplate('postgres'), model_id: id, name: id },
			fingerprint: id,
			validation: null,
			certification: null,
			created_by: 'u',
			created_at: '2026-01-01',
		},
	],
});
let client: QueryClient;
beforeEach(() => {
	vi.clearAllMocks();
	localStorage.clear();
	client = new QueryClient({
		defaultOptions: { queries: { retry: false, staleTime: Infinity } },
	});
	vi.mocked(governedMetricsApi.config).mockResolvedValue({ data: { enabled: true } });
	vi.mocked(governedMetricsApi.list).mockResolvedValue({ data: [detail('A'), detail('B')] });
	vi.mocked(governedMetricsApi.get).mockImplementation(async (id) => ({ data: detail(id) }));
});
afterEach(() => {
	cleanup();
	client.clear();
});
const mount = () =>
	render(
		<QueryClientProvider client={client}>
			<MetricsWorkspace />
		</QueryClientProvider>,
	);
const open = async (id: string) => {
	fireEvent.click(await screen.findByRole('button', { name: new RegExp(`^${id}v1`) }));
	await screen.findByRole('textbox', { name: 'Draft name' });
};

it('updates A cache without replacing B or clearing its dirty draft after a late transition', async () => {
	let finish!: (value: { data: ModelDetail }) => void;
	vi.mocked(governedMetricsApi.transition).mockImplementation(
		() =>
			new Promise((resolve) => {
				finish = resolve;
			}),
	);
	mount();
	await open('A');
	fireEvent.click(screen.getByRole('button', { name: 'Validate saved revision' }));
	await open('B');
	fireEvent.change(screen.getByRole('textbox', { name: 'Draft name' }), {
		target: { value: 'B local draft' },
	});
	await act(async () => {
		finish({ data: detail('A', 2) });
	});
	expect((screen.getByRole('textbox', { name: 'Draft name' }) as HTMLInputElement).value).toBe(
		'B local draft',
	);
	expect(screen.getByText('Unsaved changes · certification disabled')).toBeTruthy();
	expect(client.getQueryData<ModelDetail>(['governed-metrics', 'model', 'A'])?.version).toBe(2);
});

it('updates the active workspace normally and ignores a completion after unmount', async () => {
	vi.mocked(governedMetricsApi.transition).mockResolvedValue({ data: detail('A', 2) });
	const view = mount();
	await open('A');
	fireEvent.click(screen.getByRole('button', { name: 'Validate saved revision' }));
	await waitFor(() => expect(screen.getByText(/Model version 2/)).toBeTruthy());
	let finish!: (value: { data: ModelDetail }) => void;
	vi.mocked(governedMetricsApi.transition).mockImplementation(
		() =>
			new Promise((resolve) => {
				finish = resolve;
			}),
	);
	fireEvent.click(screen.getByRole('button', { name: 'Validate saved revision' }));
	view.unmount();
	await act(async () => {
		finish({ data: detail('A', 3) });
	});
	expect(client.getQueryData<ModelDetail>(['governed-metrics', 'model', 'A'])?.version).toBe(3);
});

it('recovers edits and unapplied JSON after remount without overwriting a newer server version', async () => {
	let view = mount();
	await open('A');
	fireEvent.change(screen.getByRole('textbox', { name: 'Draft name' }), {
		target: { value: 'A recovered draft' },
	});
	fireEvent.change(screen.getByRole('textbox', { name: 'Pending JSON' }), {
		target: { value: '{ "unfinished":' },
	});
	const leave = new Event('beforeunload', { cancelable: true });
	window.dispatchEvent(leave);
	expect(leave.defaultPrevented).toBe(true);
	view.unmount();
	client.clear();
	vi.mocked(governedMetricsApi.get).mockImplementation(async (id) => ({ data: detail(id, 2) }));
	view = mount();
	await open('A');
	fireEvent.click(screen.getByRole('button', { name: 'Restore browser draft' }));
	expect((screen.getByRole('textbox', { name: 'Draft name' }) as HTMLInputElement).value).toBe(
		'A recovered draft',
	);
	expect(
		(screen.getByRole('textbox', { name: 'Pending JSON' }) as HTMLTextAreaElement).value,
	).toBe('{ "unfinished":');
	expect(screen.getByText(/The recovered draft was based on server version 1/)).toBeTruthy();
	expect(
		(screen.getByRole('button', { name: 'Save new draft revision' }) as HTMLButtonElement)
			.disabled,
	).toBe(true);
	fireEvent.click(screen.getByRole('button', { name: 'Discard edits' }));
	fireEvent.change(screen.getByRole('textbox', { name: 'Draft name' }), {
		target: { value: 'updated from version 2' },
	});
	vi.mocked(governedMetricsApi.save).mockResolvedValue({ data: detail('A', 3) });
	fireEvent.click(screen.getByRole('button', { name: 'Save new draft revision' }));
	await waitFor(() =>
		expect(governedMetricsApi.save).toHaveBeenCalledWith(
			expect.objectContaining({ name: 'updated from version 2' }),
			2,
		),
	);
	await waitFor(() => expect(localStorage.length).toBe(0));
	view.unmount();
});

it('keeps the original expected version on recovery and retains edits after a 409', async () => {
	let view = mount();
	await open('A');
	fireEvent.change(screen.getByRole('textbox', { name: 'Draft name' }), {
		target: { value: 'A local' },
	});
	view.unmount();
	client.clear();
	vi.mocked(governedMetricsApi.get).mockImplementation(async (id) => ({ data: detail(id, 2) }));
	vi.mocked(governedMetricsApi.save).mockResolvedValue({
		error: true,
		status: 409,
		message: 'Version conflict',
	} as Awaited<ReturnType<typeof governedMetricsApi.save>>);
	view = mount();
	await open('A');
	fireEvent.click(screen.getByRole('button', { name: 'Restore browser draft' }));
	fireEvent.click(screen.getByRole('button', { name: 'Save new draft revision' }));
	await waitFor(() =>
		expect(governedMetricsApi.save).toHaveBeenCalledWith(
			expect.objectContaining({ name: 'A local' }),
			1,
		),
	);
	expect((screen.getByRole('textbox', { name: 'Draft name' }) as HTMLInputElement).value).toBe(
		'A local',
	);
	expect(localStorage.length).toBe(1);
	view.unmount();
});

it('lists recoverable new models that have never reached the server', async () => {
	let view = mount();
	await screen.findByRole('button', { name: /^Av1/ });
	fireEvent.click(screen.getByRole('button', { name: 'New model' }));
	fireEvent.change(screen.getByRole('textbox', { name: 'Draft name' }), {
		target: { value: 'Unsaved revenue' },
	});
	view.unmount();
	view = mount();
	fireEvent.click(await screen.findByRole('button', { name: 'Recover Unsaved revenue' }));
	fireEvent.click(screen.getByRole('button', { name: 'Restore browser draft' }));
	expect((screen.getByRole('textbox', { name: 'Draft name' }) as HTMLInputElement).value).toBe(
		'Unsaved revenue',
	);
	view.unmount();
});
