// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MetricQueryPanel } from './MetricQueryPanel';
import { governedMetricsApi } from '@/api/governedMetrics';
import { modelTemplate } from '@/lib/governedMetrics';
vi.mock('@/common/icons', () => ({ Icon: () => <span />, IconName: {} }));
vi.mock('@/api/governedMetrics', () => ({ governedMetricsApi: { query: vi.fn() } }));
const model = modelTemplate('postgres');
beforeEach(() => {
	vi.clearAllMocks();
	vi.mocked(governedMetricsApi.query).mockResolvedValue({
		error: true,
		message: 'Synthetic rejection',
	} as Awaited<ReturnType<typeof governedMetricsApi.query>>);
});
afterEach(cleanup);
const mount = () => {
	render(
		<MetricQueryPanel model={model} revision={1} active enabled canEdit hasUnsaved={false} />,
	);
	fireEvent.click(screen.getAllByRole('checkbox')[0]);
};
it('blocks invalid row limits with accessible inline feedback, allowing both boundaries', async () => {
	mount();
	fireEvent.click(screen.getByLabelText(/All time/));
	const limit = screen.getByRole('spinbutton', { name: /Row limit/ });
	for (const value of ['0', '1.5', '10001']) {
		fireEvent.change(limit, { target: { value } });
		fireEvent.click(screen.getByRole('button', { name: 'Execute certified query' }));
		expect(governedMetricsApi.query).not.toHaveBeenCalled();
		expect(limit.getAttribute('aria-invalid')).toBe('true');
		expect(
			document.getElementById(limit.getAttribute('aria-describedby')!)?.textContent,
		).toContain('Row limit');
	}
	for (const value of ['1', '10000']) {
		fireEvent.change(limit, { target: { value } });
		fireEvent.click(screen.getByRole('button', { name: 'Execute certified query' }));
		await waitFor(() =>
			expect(governedMetricsApi.query).toHaveBeenLastCalledWith(
				model.model_id,
				'execute',
				expect.objectContaining({ limit: Number(value) }),
				1,
				false,
			),
		);
		await waitFor(() => expect(screen.queryByText('Processing governed query…')).toBeNull());
	}
});
it('shows invalid time and server validation messages at their fields', async () => {
	mount();
	const start = screen.getByRole('textbox', { name: /Start \(inclusive\)/ });
	fireEvent.change(start, { target: { value: 'not a date' } });
	fireEvent.click(screen.getByRole('button', { name: 'Execute certified query' }));
	expect(governedMetricsApi.query).not.toHaveBeenCalled();
	expect(start.getAttribute('aria-invalid')).toBe('true');
	fireEvent.click(screen.getByLabelText(/All time/));
	vi.mocked(governedMetricsApi.query).mockResolvedValue({
		error: true,
		message: 'query.limit: server row policy',
		details: [{ loc: ['body', 'query', 'limit'], msg: 'server row policy' }],
	} as Awaited<ReturnType<typeof governedMetricsApi.query>>);
	fireEvent.click(screen.getByRole('button', { name: 'Execute certified query' }));
	await waitFor(() =>
		expect(
			screen.getByRole('spinbutton', { name: /Row limit/ }).getAttribute('aria-invalid'),
		).toBe('true'),
	);
	expect(screen.getByText('server row policy')).toBeTruthy();
});
