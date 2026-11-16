// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useMemo } from 'react';
type QueryRow = Record<string, unknown> | unknown[];
const cellText = (value: unknown): string =>
	value == null ? 'NULL' : typeof value === 'object' ? JSON.stringify(value) : String(value);
import { EmptyState } from '@/common/EmptyState';
import { Table, TABLE_THEAD_CLASSNAME } from '@/common/Table';
import { EmptyStateVariant } from '@/enums/emptyState';
import { usePagination } from '@/hooks/usePagination';
import type { TableColumn } from '@/types/table';

type DynamicTableProps = {
	table: { columns: string[]; rows: QueryRow[] };
};

export const DynamicTable = ({ table }: DynamicTableProps) => {
	const { columns, rows } = table;
	const { skip, pageSize, pagination } = usePagination({ totalItems: rows.length });
	// A fresh array every render would re-render `Table` on every parent render,
	// which chat result sets are large enough to feel.
	const pageRows = useMemo(() => rows.slice(skip, skip + pageSize), [rows, skip, pageSize]);

	const tableColumns = useMemo<TableColumn<QueryRow>[]>(
		() =>
			columns.map((col, index) => ({
				key: String(index),
				header: col,
				truncate: true,
				maxWidthClass: 'max-w-[240px]',
				className: 'text-body dark:text-zinc-300',
				cell: (row) => cellText(Array.isArray(row) ? row[index] : row[col]),
				title: (row) => cellText(Array.isArray(row) ? row[index] : row[col]),
			})),
		[columns],
	);

	if (columns.length === 0) {
		return <EmptyState variant={EmptyStateVariant.Inline} title="No Data Available" />;
	}

	return (
		<Table
			columns={tableColumns}
			rows={pageRows}
			showEmptyHeaders
			emptyMessage="No rows matched this query."
			rowKey={(_, index) => String(index)}
			layout="auto"
			textClassName="text-xs"
			cellClassName="px-3 py-2"
			containerClassName="overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-900"
			theadClassName={`sticky top-0 ${TABLE_THEAD_CLASSNAME}`}
			bodyClassName="text-body dark:text-zinc-300"
			rowClassName="border-b border-zinc-100 last:border-b-0 dark:border-zinc-800"
			scrollClassName="max-h-[478px] overflow-auto"
			pagination={pagination}
		/>
	);
};
