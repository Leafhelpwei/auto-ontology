// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { TooltipProvider } from '@nvidia/foundations-react-core';
import { DynamicTable } from './DynamicTable';
vi.mock('@/common/icons', () => ({ Icon: () => <span />, IconName: {} }));
beforeEach(() =>
	vi.stubGlobal(
		'ResizeObserver',
		class {
			observe() {}
			unobserve() {}
			disconnect() {}
		},
	),
);
afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});
it('renders only the current page of 10k by 30 rows and preserves precision and duplicate headers', () => {
	const columns = Array.from({ length: 30 }, (_, i) => (i < 2 ? 'amount' : `column_${i}`));
	const exact = '12345678901234567890.12345678901234567890';
	const rows = Array.from({ length: 10000 }, (_, i) => [
		String(i),
		exact,
		...Array.from({ length: 28 }, () => null),
	]);
	const view = render(
		<TooltipProvider>
			<DynamicTable table={{ columns, rows }} />
		</TooltipProvider>,
	);
	expect(view.container.querySelectorAll('tbody tr')).toHaveLength(10);
	expect(view.container.querySelectorAll('td')).toHaveLength(300);
	expect(screen.getAllByText(exact)).toHaveLength(10);
	fireEvent.click(screen.getByRole('button', { name: /next/i }));
	expect(view.container.querySelector('tbody td')?.textContent).toBe('10');
	expect(screen.getAllByText(exact)).toHaveLength(10);
});
it('retains column names on empty results', () => {
	render(<DynamicTable table={{ columns: ['amount', 'currency'], rows: [] }} />);
	expect(screen.getByRole('columnheader', { name: 'amount' })).toBeTruthy();
	expect(screen.getByText('No rows matched this query.')).toBeTruthy();
});
