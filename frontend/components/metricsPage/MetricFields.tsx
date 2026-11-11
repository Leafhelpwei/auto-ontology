// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useId, type ReactNode } from 'react';
import { Button } from '@/common/Button';
import { ButtonTheme, Size } from '@/enums/button';
import { predicateScalar } from '@/lib/governedMetrics';
import type { Predicate, SourceBinding } from '@/types/governedMetrics';

export const inputClass =
	'w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-heading outline-none focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900] disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100';
export const panelClass =
	'rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900';

export const Field = ({
	label,
	children,
	hint,
}: {
	label: string;
	children: ReactNode;
	hint?: string;
}) => (
	<label className="flex min-w-0 flex-col gap-1.5 text-sm font-medium">
		<span>{label}</span>
		{children}
		{hint && (
			<span className="text-xs font-normal text-secondary dark:text-zinc-400">{hint}</span>
		)}
	</label>
);
export const TextField = ({
	label,
	value,
	onChange,
	hint,
	disabled = false,
	type = 'text',
}: {
	label: string;
	value: string | number;
	onChange: (value: string) => void;
	hint?: string;
	disabled?: boolean;
	type?: string;
}) => (
	<Field label={label} hint={hint}>
		<input
			className={inputClass}
			type={type}
			value={value}
			onChange={(event) => onChange(event.target.value)}
			disabled={disabled}
		/>
	</Field>
);
export const SelectField = ({
	label,
	value,
	options,
	onChange,
	hint,
	disabled = false,
}: {
	label: string;
	value: string;
	options: { value: string; label: string }[];
	onChange: (value: string) => void;
	hint?: string;
	disabled?: boolean;
}) => (
	<Field label={label} hint={hint}>
		<select
			className={inputClass}
			value={value}
			disabled={disabled}
			onChange={(event) => onChange(event.target.value)}
		>
			{options.map((option) => (
				<option key={option.value} value={option.value}>
					{option.label}
				</option>
			))}
		</select>
	</Field>
);
export const CheckField = ({
	label,
	checked,
	onChange,
	disabled = false,
}: {
	label: string;
	checked: boolean;
	onChange: (value: boolean) => void;
	disabled?: boolean;
}) => (
	<label className="flex items-center gap-2 text-sm">
		<input
			type="checkbox"
			className="accent-[#76b900]"
			checked={checked}
			onChange={(event) => onChange(event.target.checked)}
			disabled={disabled}
		/>
		{label}
	</label>
);
export const ChoiceSet = ({
	label,
	options,
	value,
	onChange,
}: {
	label: string;
	options: string[];
	value: string[];
	onChange: (value: string[]) => void;
}) => (
	<fieldset className="space-y-2">
		<legend className="mb-2 text-sm font-medium">{label}</legend>
		<div className="flex flex-wrap gap-4">
			{Array.from(new Set([...options, ...value])).map((option) => (
				<CheckField
					key={option}
					label={option}
					checked={value.includes(option)}
					onChange={(checked) =>
						onChange(
							checked ? [...value, option] : value.filter((item) => item !== option),
						)
					}
				/>
			))}
			{options.length === 0 && value.length === 0 && (
				<span className="text-xs text-secondary">No options defined.</span>
			)}
		</div>
	</fieldset>
);
export const options = (values: string[]) => values.map((value) => ({ value, label: value }));

export const PredicateEditor = ({
	label,
	value,
	sources,
	onChange,
}: {
	label: string;
	value: Predicate[];
	sources: SourceBinding[];
	onChange: (value: Predicate[]) => void;
}) => {
	const id = useId();
	const update = (index: number, patch: Partial<Predicate>) =>
		onChange(value.map((item, at) => (at === index ? { ...item, ...patch } : item)));
	return (
		<fieldset className="space-y-3">
			<legend className="mb-2 text-sm font-medium">{label}</legend>
			{value.map((predicate, index) => (
				<div
					key={`${id}-${index}`}
					className="grid gap-3 rounded-lg bg-zinc-50 p-3 md:grid-cols-4 dark:bg-zinc-950"
				>
					<SelectField
						label="Source"
						value={predicate.field.source_id}
						options={[
							{ value: '', label: 'Select source' },
							...options(sources.map((source) => source.source_id)),
						]}
						onChange={(source_id) =>
							update(index, { field: { source_id, field_id: '' } })
						}
					/>
					<SelectField
						label="Field"
						value={predicate.field.field_id}
						options={[
							{ value: '', label: 'Select field' },
							...options(
								sources
									.find(
										(source) => source.source_id === predicate.field.source_id,
									)
									?.fields.map((field) => field.field_id) ?? [],
							),
						]}
						onChange={(field_id) =>
							update(index, { field: { ...predicate.field, field_id } })
						}
					/>
					<SelectField
						label="Operator"
						value={predicate.operator}
						options={options([
							'eq',
							'ne',
							'gt',
							'gte',
							'lt',
							'lte',
							'in',
							'not_in',
							'is_null',
							'is_not_null',
						])}
						onChange={(operator) =>
							update(index, {
								operator: operator as Predicate['operator'],
								value: ['is_null', 'is_not_null'].includes(operator)
									? null
									: ['in', 'not_in'].includes(operator)
										? []
										: '',
							})
						}
					/>
					{!['is_null', 'is_not_null'].includes(predicate.operator) && (
						<TextField
							label={
								['in', 'not_in'].includes(predicate.operator)
									? 'Values (comma separated)'
									: 'Value'
							}
							value={
								Array.isArray(predicate.value)
									? predicate.value.join(', ')
									: String(predicate.value ?? '')
							}
							onChange={(raw) => {
								const type = sources
									.find(
										(source) => source.source_id === predicate.field.source_id,
									)
									?.fields.find(
										(field) => field.field_id === predicate.field.field_id,
									)?.data_type;
								// Numeric values stay decimal strings; the backend binds them
								// as Decimal without a JavaScript Number rounding step.
								const scalar = (part: string) => predicateScalar(part, type);
								update(index, {
									value: ['in', 'not_in'].includes(predicate.operator)
										? raw.split(',').map((part) => scalar(part.trim()))
										: scalar(raw),
								});
							}}
						/>
					)}
					<Button
						theme={ButtonTheme.Minimal}
						size={Size.SMALL}
						onClick={() => onChange(value.filter((_, at) => at !== index))}
					>
						Remove filter
					</Button>
				</div>
			))}
			<Button
				theme={ButtonTheme.Secondary}
				size={Size.SMALL}
				onClick={() =>
					onChange([
						...value,
						{
							field: { source_id: sources[0]?.source_id ?? '', field_id: '' },
							operator: 'eq',
							value: '',
						},
					])
				}
			>
				Add filter
			</Button>
		</fieldset>
	);
};
