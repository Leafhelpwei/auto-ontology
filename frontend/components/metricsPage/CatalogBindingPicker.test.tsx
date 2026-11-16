// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CatalogBindingPicker, TermPicker } from './CatalogBindingPicker';
import { newSource } from '@/lib/governedMetrics';
import type { SourceBinding } from '@/types/governedMetrics';
const mocks = vi.hoisted(() => ({
	databases: vi.fn(),
	schemas: vi.fn(),
	tables: vi.fn(),
	columns: vi.fn(),
	terms: vi.fn(),
	term: vi.fn(),
}));
vi.mock('@/api/datasources', () => ({
	datasources: {
		getDBs: mocks.databases,
		getSchemasForDatabase: mocks.schemas,
		getTablesForSchema: mocks.tables,
		getColumnsForTable: mocks.columns,
	},
}));
vi.mock('@/api/terms', () => ({ termsApi: { list: mocks.terms, get: mocks.term } }));
const columns = [
	{ id: 'column-order', column_name: 'order_id', data_type: 'bigint' },
	{ id: 'column-amount', column_name: 'amount', data_type: 'decimal(20,4)' },
	{ id: 'column-interval', column_name: 'duration', data_type: 'interval' },
];
beforeEach(() => {
	vi.resetAllMocks();
	mocks.databases.mockResolvedValue({ data: [{ id: 'db-orders', name: 'Sales' }] });
	mocks.schemas.mockResolvedValue({ data: [{ id: 'schema-orders', schema_name: 'public' }] });
	mocks.tables.mockResolvedValue({ data: [{ id: 'table-orders', name: 'Orders' }] });
	mocks.columns.mockResolvedValue({ data: columns, total: columns.length });
	mocks.terms.mockResolvedValue({
		terms: [{ id: 'term-orders', name: 'Order revenue' }],
		total: 1,
	});
	mocks.term.mockResolvedValue({ data: { id: 'term-orders', name: 'Order revenue' } });
});
afterEach(cleanup);
const mount = (element: React.ReactNode) =>
	render(
		<QueryClientProvider
			client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}
		>
			{element}
		</QueryClientProvider>,
	);
const chooseTable = async () => {
	await screen.findByRole('option', { name: 'Sales' });
	fireEvent.change(screen.getByLabelText('Catalog database'), { target: { value: 'db-orders' } });
	await screen.findByRole('option', { name: 'public' });
	fireEvent.change(screen.getByLabelText('Catalog schema'), {
		target: { value: 'schema-orders' },
	});
	await screen.findByRole('option', { name: 'Orders' });
	fireEvent.change(screen.getByLabelText('Catalog table'), { target: { value: 'table-orders' } });
	await screen.findByLabelText('order_id · bigint');
};
it('binds Catalog names, IDs and logical types from labels and explicit grain without certifying', async () => {
	const apply = vi.fn((_source: SourceBinding) => true),
		close = vi.fn();
	mount(<CatalogBindingPicker source={newSource(1)} onApply={apply} onClose={close} />);
	await chooseTable();
	for (const column of columns)
		fireEvent.click(screen.getByLabelText(`${column.column_name} · ${column.data_type}`));
	fireEvent.click(screen.getByLabelText('Grain: order_id'));
	fireEvent.click(screen.getByRole('button', { name: 'Apply Catalog binding' }));
	await waitFor(() => expect(apply).toHaveBeenCalledOnce());
	expect(apply.mock.calls[0]?.[0]).toMatchObject({
		datasource_id: 'db-orders',
		catalog_table_id: 'table-orders',
		table_name: 'Orders',
		schema_name: 'public',
		grain: ['order_id'],
		structure_fingerprint: '',
		fields: [
			{ catalog_column_id: 'column-order', data_type: 'number' },
			{ catalog_column_id: 'column-amount', data_type: 'number' },
			{ catalog_column_id: 'column-interval', data_type: 'string' },
		],
	});
	expect(mocks.columns).toHaveBeenCalledTimes(2);
	expect(close).toHaveBeenCalledOnce();
});
it.each(['missing', 'denied'])(
	'rejects %s Catalog objects during the fresh apply check',
	async (failure) => {
		const apply = vi.fn(() => true);
		mount(<CatalogBindingPicker source={newSource(1)} onApply={apply} onClose={vi.fn()} />);
		await chooseTable();
		fireEvent.click(screen.getByLabelText('amount · decimal(20,4)'));
		mocks.tables.mockResolvedValue(
			failure === 'missing' ? { data: [] } : { error: true, message: 'Access denied' },
		);
		fireEvent.click(screen.getByRole('button', { name: 'Apply Catalog binding' }));
		expect((await screen.findByRole('alert')).textContent).toMatch(
			/no longer available|Access denied/,
		);
		expect(apply).not.toHaveBeenCalled();
	},
);
it('ignores an apply that completes after the picker is closed', async () => {
	const apply = vi.fn(() => true);
	const view = mount(
		<CatalogBindingPicker source={newSource(1)} onApply={apply} onClose={vi.fn()} />,
	);
	await chooseTable();
	fireEvent.click(screen.getByLabelText('amount · decimal(20,4)'));
	let finish!: (value: unknown) => void;
	mocks.columns.mockReturnValue(
		new Promise((resolve) => {
			finish = resolve;
		}),
	);
	fireEvent.click(screen.getByRole('button', { name: 'Apply Catalog binding' }));
	view.unmount();
	await act(async () => finish({ data: columns, total: columns.length }));
	expect(apply).not.toHaveBeenCalled();
});
it('pages columns and retains selections across searches', async () => {
	const extra = Array.from({ length: 45 }, (_, index) => ({
		id: `extra-${index}`,
		column_name: `extra_${index}`,
		data_type: 'int',
	}));
	mocks.columns.mockResolvedValue({ data: [...columns, ...extra], total: 48 });
	mount(
		<CatalogBindingPicker
			source={newSource(1)}
			onApply={vi.fn(() => true)}
			onClose={vi.fn()}
		/>,
	);
	await chooseTable();
	expect(screen.getAllByRole('checkbox')).toHaveLength(40);
	fireEvent.click(screen.getByLabelText('order_id · bigint'));
	fireEvent.click(screen.getByRole('button', { name: 'Next columns' }));
	expect(screen.queryByLabelText('order_id · bigint')).toBeNull();
	fireEvent.change(screen.getByLabelText('Search Catalog columns'), {
		target: { value: 'order_id' },
	});
	expect((screen.getByLabelText('order_id · bigint') as HTMLInputElement).checked).toBe(true);
	expect(screen.getAllByRole('checkbox')).toHaveLength(2);
});
it('selects terms by name with fresh access checks and ignores a cancelled late selection', async () => {
	const selected = vi.fn();
	mount(<TermPicker onSelect={selected} />);
	fireEvent.click(screen.getByRole('button', { name: 'Choose business term' }));
	fireEvent.click(await screen.findByRole('button', { name: 'Order revenue' }));
	await waitFor(() => expect(selected).toHaveBeenCalledWith('term-orders'));
	selected.mockClear();
	fireEvent.click(screen.getByRole('button', { name: 'Choose business term' }));
	let finish!: (value: unknown) => void;
	mocks.term.mockReturnValue(
		new Promise((resolve) => {
			finish = resolve;
		}),
	);
	fireEvent.click(await screen.findByRole('button', { name: 'Order revenue' }));
	fireEvent.click(screen.getByRole('button', { name: 'Close term picker' }));
	await act(async () => finish({ data: { id: 'term-orders' } }));
	expect(selected).not.toHaveBeenCalled();
});
