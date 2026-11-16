// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { datasources } from '@/api/datasources';
import { termsApi } from '@/api/terms';
import { Button } from '@/common/Button';
import { Modal } from '@/common/modal/Modal';
import { ButtonTheme } from '@/enums/button';
import { usePagination } from '@/hooks/usePagination';
import { nextStableIndex } from '@/lib/modelIntegrity';
import type { Column } from '@/types/datasources';
import type { SourceBinding } from '@/types/governedMetrics';
import { CheckField, SelectField, TextField } from './MetricFields';

const unwrap = <T,>(value: { data?: T; error?: boolean; message?: string }): T => {
	if (value.error || value.data == null)
		throw new Error(value.message ?? 'Catalog object is unavailable or access was denied.');
	return value.data;
};
const readColumns = async (tableId: string): Promise<Column[]> => {
	const columns: Column[] = [];
	for (let skip = 0; skip < 5000; skip += 100) {
		const response = await datasources.getColumnsForTable(tableId, { skip, limit: 100 });
		const page = unwrap(response);
		columns.push(...page);
		if (columns.length >= response.total || page.length < 100) return columns;
	}
	throw new Error('This table exceeds the 5,000-column picker budget. Choose a narrower view.');
};
const logicalType = (physical: string | null): SourceBinding['fields'][number]['data_type'] => {
	const type = (physical ?? '').toLowerCase();
	if (/^bool(?:ean)?$/.test(type)) return 'boolean';
	if (/timestamp|datetime/.test(type)) return 'datetime';
	if (/^date$/.test(type)) return 'date';
	if (
		/^(?:tinyint|smallint|mediumint|bigint|int(?:eger|[248])?|numeric|decimal|float[48]?|double(?: precision)?|real)(?:\b|\()/.test(
			type,
		)
	)
		return 'number';
	return 'string';
};

export const CatalogBindingPicker = ({
	source,
	onApply,
	onClose,
}: {
	source: SourceBinding;
	onApply: (source: SourceBinding) => boolean;
	onClose: () => void;
}) => {
	const [databaseId, setDatabaseId] = useState(source.datasource_id);
	const [schemaId, setSchemaId] = useState('');
	const [tableId, setTableId] = useState('');
	const [search, setSearch] = useState('');
	const [columnSearch, setColumnSearch] = useState('');
	const [selected, setSelected] = useState<string[]>([]);
	const [grain, setGrain] = useState<string[]>([]);
	const [error, setError] = useState('');
	const [busy, setBusy] = useState(false);
	const mounted = useRef(true);
	useEffect(() => {
		mounted.current = true;
		return () => {
			mounted.current = false;
		};
	}, []);
	const databases = useQuery({
		queryKey: ['catalog-model-picker', 'databases'],
		queryFn: async () => unwrap(await datasources.getDBs()),
		retry: false,
	});
	const schemas = useQuery({
		queryKey: ['catalog-model-picker', 'schemas', databaseId],
		enabled: Boolean(databaseId),
		queryFn: async () => unwrap(await datasources.getSchemasForDatabase(databaseId)),
		retry: false,
	});
	const tables = useQuery({
		queryKey: ['catalog-model-picker', 'tables', schemaId],
		enabled: Boolean(schemaId),
		queryFn: async () => unwrap(await datasources.getTablesForSchema(schemaId)),
		retry: false,
	});
	const columns = useQuery({
		queryKey: ['catalog-model-picker', 'columns', tableId],
		enabled: Boolean(tableId),
		queryFn: () => readColumns(tableId),
		retry: false,
	});
	const matchingColumns = (columns.data ?? []).filter((column) =>
		column.column_name.toLocaleLowerCase().includes(columnSearch.toLocaleLowerCase()),
	);
	const paging = usePagination({
		totalItems: matchingColumns.length,
		pageSize: 20,
		resetKey: `${tableId}:${columnSearch}`,
	});
	const resetTable = () => {
		setTableId('');
		setSelected([]);
		setGrain([]);
		setError('');
	};
	const apply = async () => {
		setBusy(true);
		setError('');
		try {
			// Re-read all ancestors and columns before binding: cached labels never grant access.
			const [dbs, schemaList, tableList, currentColumns] = await Promise.all([
				datasources.getDBs().then(unwrap),
				datasources.getSchemasForDatabase(databaseId).then(unwrap),
				datasources.getTablesForSchema(schemaId).then(unwrap),
				readColumns(tableId),
			]);
			const database = dbs.find((item) => item.id === databaseId),
				schema = schemaList.find((item) => item.id === schemaId),
				table = tableList.find((item) => item.id === tableId);
			if (
				!database ||
				!schema ||
				!table ||
				selected.some((id) => !currentColumns.some((column) => column.id === id))
			)
				throw new Error(
					'A selected Catalog object is no longer available. Reload the selection.',
				);
			const occupied = new Set<string>();
			const fields = currentColumns
				.filter((column) => selected.includes(column.id))
				.map((column) => {
					const previous =
						source.catalog_table_id === tableId
							? source.fields.find((field) => field.catalog_column_id === column.id)
							: undefined;
					let field_id = previous?.field_id ?? column.column_name;
					if (occupied.has(field_id))
						field_id = `field_${nextStableIndex('field', [...occupied])}`;
					occupied.add(field_id);
					return {
						...previous,
						field_id,
						catalog_column_id: column.id,
						column_name: column.column_name,
						data_type: logicalType(column.data_type),
						nullable: previous?.nullable ?? true,
						datetime_timezone: previous?.datetime_timezone ?? null,
					};
				});
			const next = {
				...source,
				datasource_id: database.id,
				catalog_table_id: table.id,
				schema_name: schema.schema_name,
				table_name: table.name,
				fields,
				grain: fields
					.filter((field) => grain.includes(field.catalog_column_id))
					.map((field) => field.field_id),
				structure_fingerprint: '',
			};
			if (!mounted.current) return;
			if (onApply(next)) onClose();
			else
				setError(
					'This selection would break model references. Close this dialog to review the dependency list, or retain the referenced columns.',
				);
		} catch (error) {
			if (mounted.current)
				setError(error instanceof Error ? error.message : 'Catalog binding failed.');
		} finally {
			if (mounted.current) setBusy(false);
		}
	};
	const failure =
		error ||
		databases.error?.message ||
		schemas.error?.message ||
		tables.error?.message ||
		columns.error?.message;
	return (
		<Modal
			open
			onClose={onClose}
			ariaLabel="Bind source from Catalog"
			className="flex max-h-[90dvh] w-3xl max-w-full flex-col overflow-y-auto p-5"
		>
			<h2 className="text-lg font-semibold">Bind source from Catalog</h2>
			<p className="my-3 text-sm text-secondary">
				Select columns and grain explicitly. Suggested logical types, unique keys and
				relationships require server validation; binding does not certify a model. Review
				datetime timezones after binding.
			</p>
			<fieldset disabled={busy} className="space-y-3">
				<div className="grid gap-3 sm:grid-cols-2">
					<SelectField
						label="Catalog database"
						value={databaseId}
						disabled={databases.isPending}
						options={[
							{ value: '', label: 'Select database' },
							...(databases.data ?? []).map((item) => ({
								value: item.id,
								label: item.name,
							})),
						]}
						onChange={(value) => {
							setDatabaseId(value);
							setSchemaId('');
							resetTable();
						}}
					/>
					<SelectField
						label="Catalog schema"
						value={schemaId}
						disabled={!databaseId || schemas.isPending}
						options={[
							{ value: '', label: 'Select schema' },
							...(schemas.data ?? []).map((item) => ({
								value: item.id,
								label: item.schema_name,
							})),
						]}
						onChange={(value) => {
							setSchemaId(value);
							resetTable();
						}}
					/>
				</div>
				<TextField
					label="Search Catalog tables"
					value={search}
					onChange={(value) => {
						setSearch(value);
						resetTable();
					}}
				/>
				<SelectField
					label="Catalog table"
					value={tableId}
					disabled={!schemaId || tables.isPending}
					options={[
						{ value: '', label: 'Select table' },
						...(tables.data ?? [])
							.filter((item) =>
								item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
							)
							.map((item) => ({ value: item.id, label: item.name })),
					]}
					onChange={(value) => {
						setTableId(value);
						setSelected([]);
						setGrain([]);
						setError('');
					}}
				/>
				{tableId && (
					<>
						<TextField
							label="Search Catalog columns"
							value={columnSearch}
							onChange={setColumnSearch}
						/>
						<p className="text-xs">
							{selected.length} selected columns · {grain.length} grain columns
						</p>
						{columns.isPending && <p role="status">Loading Catalog columns…</p>}
						<div className="max-h-64 space-y-3 overflow-y-auto">
							{matchingColumns
								.slice(paging.skip, paging.skip + paging.pageSize)
								.map((column) => (
									<div
										key={column.id}
										className="flex flex-wrap items-center justify-between gap-2 rounded border border-zinc-200 p-2 dark:border-zinc-700"
									>
										<CheckField
											label={`${column.column_name} · ${column.data_type ?? 'unknown type'}`}
											checked={selected.includes(column.id)}
											onChange={(checked) => {
												setSelected(
													checked
														? [...selected, column.id]
														: selected.filter((id) => id !== column.id),
												);
												if (!checked)
													setGrain(
														grain.filter((id) => id !== column.id),
													);
											}}
										/>
										<CheckField
											label={`Grain: ${column.column_name}`}
											disabled={!selected.includes(column.id)}
											checked={grain.includes(column.id)}
											onChange={(checked) =>
												setGrain(
													checked
														? [...grain, column.id]
														: grain.filter((id) => id !== column.id),
												)
											}
										/>
									</div>
								))}
						</div>
						<div className="flex items-center gap-3">
							<Button
								theme={ButtonTheme.Secondary}
								disabled={paging.page === 1}
								onClick={() => paging.setPage(paging.page - 1)}
							>
								Previous columns
							</Button>
							<span className="text-sm">
								Page {paging.page} · {matchingColumns.length} columns
							</span>
							<Button
								theme={ButtonTheme.Secondary}
								disabled={paging.skip + paging.pageSize >= matchingColumns.length}
								onClick={() => paging.setPage(paging.page + 1)}
							>
								Next columns
							</Button>
						</div>
					</>
				)}
			</fieldset>
			{failure && (
				<p role="alert" className="my-3 text-sm text-red-700 dark:text-red-300">
					{failure}
				</p>
			)}
			<div className="mt-4 flex justify-end gap-2">
				<Button theme={ButtonTheme.Secondary} onClick={onClose}>
					Cancel binding
				</Button>
				<Button
					disabled={busy || !tableId || !selected.length || Boolean(columns.error)}
					loading={busy}
					onClick={() => void apply()}
				>
					Apply Catalog binding
				</Button>
			</div>
		</Modal>
	);
};

const TermDialog = ({
	onSelect,
	onClose,
}: {
	onSelect: (id: string) => void;
	onClose: () => void;
}) => {
	const [search, setSearch] = useState(''),
		[page, setPage] = useState(0),
		[error, setError] = useState('');
	const selectionEpoch = useRef(0);
	useEffect(
		() => () => {
			selectionEpoch.current += 1;
		},
		[],
	);
	const result = useQuery({
		queryKey: ['metric-owner-terms', search, page],
		queryFn: async () => {
			const response = await termsApi.list({ query: search, skip: page * 20, limit: 20 });
			if (response.error) throw new Error(response.message);
			return response;
		},
		retry: false,
	});
	const select = async (id: string) => {
		const epoch = ++selectionEpoch.current;
		try {
			unwrap(await termsApi.get(id));
			if (epoch !== selectionEpoch.current) return;
			onSelect(id);
			onClose();
		} catch (error) {
			if (epoch === selectionEpoch.current)
				setError(error instanceof Error ? error.message : 'This term is unavailable.');
		}
	};
	return (
		<Modal
			open
			onClose={onClose}
			ariaLabel="Select business term"
			className="max-h-[85dvh] w-xl max-w-full space-y-3 overflow-y-auto p-5"
		>
			<h2 className="font-semibold">Select business term</h2>
			<TextField
				label="Search business terms"
				value={search}
				onChange={(value) => {
					setSearch(value);
					setPage(0);
				}}
			/>
			{(error || result.error) && (
				<p role="alert" className="text-sm text-red-700">
					{error || result.error?.message}
				</p>
			)}
			<ul className="space-y-2">
				{result.data?.terms.map((term) => (
					<li key={term.id}>
						<Button theme={ButtonTheme.Secondary} onClick={() => void select(term.id)}>
							{term.name}
						</Button>
					</li>
				))}
			</ul>
			<div className="flex flex-wrap gap-2">
				<Button
					theme={ButtonTheme.Secondary}
					disabled={page === 0}
					onClick={() => setPage(page - 1)}
				>
					Previous terms
				</Button>
				<Button
					theme={ButtonTheme.Secondary}
					disabled={(page + 1) * 20 >= (result.data?.total ?? 0)}
					onClick={() => setPage(page + 1)}
				>
					Next terms
				</Button>
				<Button theme={ButtonTheme.Secondary} onClick={onClose}>
					Close term picker
				</Button>
			</div>
		</Modal>
	);
};
export const TermPicker = ({ onSelect }: { onSelect: (id: string) => void }) => {
	const [open, setOpen] = useState(false);
	return (
		<>
			<Button theme={ButtonTheme.Secondary} onClick={() => setOpen(true)}>
				Choose business term
			</Button>
			{open && <TermDialog onSelect={onSelect} onClose={() => setOpen(false)} />}
		</>
	);
};
