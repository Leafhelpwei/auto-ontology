// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { GraphEntityList } from './GraphEntityList';
vi.mock('@/common/icons', () => ({ Icon: () => null, IconName: {} }));
afterEach(cleanup);
it('searches expanded entities, shares selection/expansion, and exposes both connection endpoints', () => {
	const onSelect = vi.fn(),
		onExpand = vi.fn(),
		onConnection = vi.fn();
	render(
		<GraphEntityList
			snapshot={{
				nodes: [
					...Array.from({ length: 25 }, (_, i) => ({
						id: `t${i}`,
						name: `Table ${i}`,
						kind: 'table',
						expanded: false,
					})),
					{ id: 'column:amount', name: 'Amount', kind: 'column', expanded: true },
				],
				edges: [
					{ id: 'edge', source: 't0', target: 'column:amount', kind: 'relationship' },
				],
				expansions: 1,
				notice: '',
			}}
			activeId="column:amount"
			onSelect={onSelect}
			onExpand={onExpand}
			onConnection={onConnection}
		/>,
	);
	expect(screen.getAllByRole('listitem')).toHaveLength(10);
	fireEvent.change(screen.getByLabelText('Search loaded graph'), { target: { value: 'Amount' } });
	const selected = screen.getByRole('button', { name: 'Amount · column' });
	expect(selected.getAttribute('aria-pressed')).toBe('true');
	selected.focus();
	fireEvent.click(selected);
	expect(onSelect).toHaveBeenCalledWith('column:amount');
	fireEvent.click(screen.getByRole('button', { name: 'Collapse Amount' }));
	expect(onExpand).toHaveBeenCalledWith('column:amount');
	fireEvent.click(screen.getByRole('button', { name: 'Connections' }));
	expect(screen.getByText('relationship · undirected connection')).toBeTruthy();
	fireEvent.click(screen.getByRole('button', { name: 'Table 0' }));
	expect(onSelect).toHaveBeenLastCalledWith('t0');
	fireEvent.click(screen.getByRole('button', { name: 'View connection path' }));
	expect(onConnection).toHaveBeenCalledWith('edge');
});
