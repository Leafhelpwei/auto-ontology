// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { atomicMetricDependencies } from '@/lib/governedMetrics';
import { queryFieldErrors } from '@/lib/metricQueryValidation';
import { validationErrors, type FieldError } from '@/api/validationErrors';
import { useState } from 'react';
import { DynamicTable } from '@/components/chatPage/DynamicTable';
import { ResultColumnDetails } from './ResultColumnDetails';
import { governedMetricsApi } from '@/api/governedMetrics';
import { Button } from '@/common/Button';
import { ButtonTheme } from '@/enums/button';
import type {
	GovernedModel,
	InterpretationResult,
	MetricQueryRequest,
	MetricQueryResult,
} from '@/types/governedMetrics';
import {
	CheckField,
	ChoiceSet,
	Field,
	inputClass,
	options,
	panelClass,
	PredicateEditor,
	SelectField,
	TextField,
} from './MetricFields';

const initialQuery: MetricQueryRequest = {
	metric_ids: [],
	dimensions: [],
	filters: [],
	time: null,
	all_time: false,
	order_by: [],
	limit: 100,
};
const QueryResult = ({ result, executed }: { result: MetricQueryResult; executed: boolean }) => {
	const rows = result.rows ?? [];
	const columns =
		result.columns ??
		result.column_metadata?.map((column) => column.name) ??
		(rows[0] && !Array.isArray(rows[0]) ? Object.keys(rows[0]) : []);
	return (
		<section className={`${panelClass} space-y-4`} aria-label="Query result">
			<div className="flex flex-wrap items-center gap-3">
				<h3 className="font-semibold">
					{executed
						? 'Executed against the datasource'
						: result.trusted
							? 'Certified query plan · not executed'
							: 'Draft preview · not certified · not executed'}
				</h3>
				<span
					className={`rounded-full px-3 py-1 text-xs ${result.trusted ? 'bg-[#76b900]/15 text-[#4d7a00] dark:text-[#a3d63a]' : 'bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200'}`}
				>
					Revision {result.revision} ·{' '}
					{result.trusted ? 'Certified' : 'Untrusted preview'}
				</span>
			</div>
			<p className="text-xs text-secondary">
				Datasource: {result.plan.datasource_id} · Sources:{' '}
				{result.plan.source_ids.join(', ')}
			</p>
			{executed && (
				<div className="space-y-2">
					<p className="text-sm">{result.row_count ?? rows.length} rows returned</p>
					<DynamicTable table={{ columns, rows }} />
				</div>
			)}
			<ResultColumnDetails columns={result.column_metadata} />
			<details open>
				<summary className="cursor-pointer text-sm font-medium">Compiled SQL</summary>
				<pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-zinc-950 p-4 font-mono text-xs text-zinc-100">
					{result.plan.sql}
				</pre>
			</details>
			<details>
				<summary className="cursor-pointer text-sm font-medium">Bound parameters</summary>
				<pre className="mt-2 overflow-auto text-xs">
					{JSON.stringify(result.plan.params, null, 2)}
				</pre>
			</details>
			<details open>
				<summary className="cursor-pointer text-sm font-medium">
					Execution evidence and semantic fingerprints
				</summary>
				<pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-zinc-50 p-4 text-xs dark:bg-zinc-950">
					{JSON.stringify(
						{
							evidence: result.plan.evidence,
							model_fingerprint: result.plan.model_fingerprint,
							metric_fingerprints: result.plan.metric_fingerprints,
							request_fingerprint: result.plan.request_fingerprint,
						},
						null,
						2,
					)}
				</pre>
			</details>
		</section>
	);
};

export const MetricQueryPanel = ({
	model,
	revision,
	active,
	enabled,
	canEdit,
	hasUnsaved,
}: {
	model: GovernedModel;
	revision: number;
	active: boolean;
	enabled: boolean;
	canEdit: boolean;
	hasUnsaved: boolean;
}) => {
	const [query, setQuery] = useState<MetricQueryRequest>(initialQuery);
	const [question, setQuestion] = useState('');
	const [interpretation, setInterpretation] = useState<InterpretationResult | null>(null);
	const [result, setResult] = useState<MetricQueryResult | null>(null);
	const [executed, setExecuted] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState('');
	const [fieldErrors, setFieldErrors] = useState<FieldError[]>([]);
	const fieldError = (path: string) =>
		fieldErrors
			.filter((item) => item.path === path)
			.map((item) => item.message)
			.join(' ');
	const atomicDependencies = atomicMetricDependencies(model, query.metric_ids);
	const patch = (value: Partial<MetricQueryRequest>) => {
		const next = { ...query, ...value };
		if (value.metric_ids && next.time?.metric_roles)
			next.time = {
				...next.time,
				metric_roles: undefined,
				time_role: atomicMetricDependencies(model, value.metric_ids)[0]?.time_role ?? '',
			};
		setQuery(next);
		if (fieldErrors.length) setFieldErrors(queryFieldErrors(next));
		setResult(null);
		setError('');
	};
	const selected = model.metrics.filter((metric) => query.metric_ids.includes(metric.metric_id));
	const dimensions = selected.length
		? selected[0].supported_dimensions.filter((dimension) =>
				selected.every((metric) => metric.supported_dimensions.includes(dimension)),
			)
		: [];
	const groups = Array.from(
		new Set(model.metrics.map((metric) => metric.family_id ?? 'Ungrouped metrics')),
	);
	const disabled = busy || !enabled || hasUnsaved;
	const run = async (action: 'compile' | 'execute', preview: boolean) => {
		setError('');
		setResult(null);
		const issues = queryFieldErrors(query);
		setFieldErrors(issues);
		if (issues.length) {
			setError('Correct the marked query fields before sending.');
			return;
		}
		setBusy(true);
		try {
			const response = await governedMetricsApi.query(
				model.model_id,
				action,
				query,
				revision,
				preview,
			);
			if (response.error) {
				setFieldErrors(
					(response.fieldErrors ?? validationErrors(response.details)).map((issue) => ({
						...issue,
						path: issue.path.replace(/^query\./, ''),
					})),
				);
				setError(
					`${response.code ? `${response.code}: ` : ''}${response.message ?? 'Query failed.'}`,
				);
			} else {
				setResult(response.data);
				setExecuted(action === 'execute');
			}
		} finally {
			setBusy(false);
		}
	};
	const interpret = async () => {
		setBusy(true);
		setError('');
		setResult(null);
		setInterpretation(null);
		try {
			const response = await governedMetricsApi.interpret(
				model.model_id,
				question,
				query.metric_ids,
				revision,
			);
			if (response.error) setError(response.message ?? 'Question interpretation failed.');
			else {
				setInterpretation(response.data);
				if (response.data.status === 'ready' && response.data.query)
					setQuery(response.data.query);
			}
		} finally {
			setBusy(false);
		}
	};
	return (
		<div className="space-y-5">
			{model.dialect === 'mysql' && (
				<p className="text-sm text-secondary">
					MySQL 5.7: DATETIME ranges and datetime grouping require a constant timezone
					offset throughout the range. Split ranges across daylight saving transitions.
					UTC TIMESTAMP range filtering may span them.
				</p>
			)}

			<div className="rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-sm dark:border-zinc-700 dark:bg-zinc-900">
				{active
					? `Queries use saved, certified active revision ${revision}.`
					: `Revision ${revision} is not the certified active revision. Editors can inspect a draft SQL preview; execution is unavailable.`}
				{hasUnsaved && (
					<p className="mt-2 font-medium text-amber-700 dark:text-amber-300">
						Save or discard model edits before compiling, interpreting, or executing.
					</p>
				)}
			</div>
			<section className={panelClass}>
				<fieldset disabled={disabled || !active} className="space-y-3 disabled:opacity-60">
					<Field
						label="Ask a governed metric question"
						hint="Interpretation can request clarification. It never falls back to unrestricted SQL."
					>
						<textarea
							className={inputClass}
							rows={2}
							value={question}
							onChange={(event) => {
								setQuestion(event.target.value);
								setInterpretation(null);
							}}
							placeholder="For example: confirmed amount by currency, all time"
						/>
					</Field>
					<Button
						theme={ButtonTheme.Secondary}
						disabled={!question.trim()}
						onClick={() => void interpret()}
					>
						Interpret question
					</Button>
				</fieldset>
				{interpretation && (
					<div role="status" className="mt-4 space-y-3 text-sm">
						<p className="font-medium">
							{interpretation.status === 'ready'
								? 'Structured request ready · review below before executing'
								: interpretation.status === 'clarification'
									? 'Clarification required'
									: 'Request rejected'}
						</p>
						<p>{interpretation.message}</p>
						{interpretation.options.length > 0 && (
							<div className="flex flex-wrap gap-2">
								{interpretation.options.map((option) => (
									<Button
										key={option.metric_id}
										theme={
											query.metric_ids.includes(option.metric_id)
												? ButtonTheme.Primary
												: ButtonTheme.Secondary
										}
										disabled={disabled}
										title={option.description}
										onClick={() =>
											patch({
												metric_ids: [option.metric_id],
												dimensions: [],
											})
										}
									>
										{option.name} · {option.metric_id}
									</Button>
								))}
							</div>
						)}
						{interpretation.status === 'clarification' && (
							<p className="text-secondary">
								Choose a variant, then interpret the question again.
							</p>
						)}
					</div>
				)}
			</section>
			<section className={panelClass}>
				<fieldset disabled={disabled} className="space-y-5 disabled:opacity-60">
					<h3 className="font-semibold">Structured query</h3>
					<div className="space-y-4">
						{groups.map((family) => (
							<fieldset
								key={family}
								className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700"
							>
								<legend className="px-2 text-sm font-medium">{family}</legend>
								<div className="space-y-3">
									{model.metrics
										.filter(
											(metric) =>
												(metric.family_id ?? 'Ungrouped metrics') ===
												family,
										)
										.map((metric) => (
											<div key={metric.metric_id}>
												<CheckField
													label={`${metric.name} (${metric.metric_id})${metric.default_of_family ? ' · family default' : ''}`}
													checked={query.metric_ids.includes(
														metric.metric_id,
													)}
													onChange={(checked) =>
														patch({
															metric_ids: checked
																? [
																		...query.metric_ids,
																		metric.metric_id,
																	]
																: query.metric_ids.filter(
																		(id) =>
																			id !== metric.metric_id,
																	),
															dimensions: [],
														})
													}
												/>
												<p className="mt-1 pl-6 text-xs text-secondary">
													{metric.description} · {metric.unit}
													{metric.fixed_currency
														? ` / ${metric.fixed_currency}`
														: ''}{' '}
													· {metric.time_role ?? 'No time role'}
												</p>
											</div>
										))}
								</div>
							</fieldset>
						))}
					</div>
					<p className="text-xs text-secondary">
						Select the exact definition. A family name alone does not select a variant;
						confirmed and paid are different business events.
					</p>
					<ChoiceSet
						label="Dimensions shared by selected metrics"
						options={dimensions}
						value={query.dimensions}
						onChange={(value) => patch({ dimensions: value })}
					/>
					<CheckField
						label="All time · explicitly no time restriction"
						checked={query.all_time}
						onChange={(all_time) =>
							patch({
								all_time,
								time: all_time
									? null
									: {
											start: '',
											end_exclusive: '',
											timezone: 'UTC',
											time_role: selected[0]?.time_role ?? '',
											granularity: null,
										},
							})
						}
					/>
					{!query.all_time && (
						<div className="grid gap-4 md:grid-cols-2">
							<TextField
								label="Start (inclusive)"
								error={fieldError('time.start')}
								value={query.time?.start ?? ''}
								hint="ISO timestamp with offset, e.g. 2026-01-01T00:00:00+08:00"
								onChange={(start) =>
									patch({
										time: {
											metric_roles: query.time?.metric_roles,
											start,
											end_exclusive: query.time?.end_exclusive ?? '',
											timezone: query.time?.timezone ?? 'UTC',
											time_role:
												query.time?.time_role ??
												selected[0]?.time_role ??
												'',
											granularity: query.time?.granularity ?? null,
										},
									})
								}
							/>
							<TextField
								label="End (exclusive)"
								error={fieldError('time.end_exclusive')}
								value={query.time?.end_exclusive ?? ''}
								hint="ISO timestamp with offset, e.g. 2026-02-01T00:00:00+08:00"
								onChange={(end_exclusive) =>
									patch({
										time: {
											metric_roles: query.time?.metric_roles,
											start: query.time?.start ?? '',
											end_exclusive,
											timezone: query.time?.timezone ?? 'UTC',
											time_role:
												query.time?.time_role ??
												selected[0]?.time_role ??
												'',
											granularity: query.time?.granularity ?? null,
										},
									})
								}
							/>
							<TextField
								label="Timezone"
								error={fieldError('time.timezone')}
								value={query.time?.timezone ?? 'UTC'}
								hint="IANA timezone, e.g. Asia/Shanghai"
								onChange={(timezone) =>
									patch({
										time: {
											metric_roles: query.time?.metric_roles,
											start: query.time?.start ?? '',
											end_exclusive: query.time?.end_exclusive ?? '',
											timezone,
											time_role:
												query.time?.time_role ??
												selected[0]?.time_role ??
												'',
											granularity: query.time?.granularity ?? null,
										},
									})
								}
							/>
							{query.time && atomicDependencies.length > 1 && (
								<div className="space-y-2 md:col-span-2">
									<CheckField
										label="Use each metric's declared business time role"
										checked={
											Object.keys(query.time.metric_roles ?? {}).length > 0
										}
										disabled={atomicMetricDependencies(
											model,
											query.metric_ids,
										).some((metric) => !metric.time_role)}
										onChange={(enabled) => {
											if (query.time)
												patch({
													time: {
														...query.time,
														time_role: enabled
															? 'per_metric'
															: (atomicMetricDependencies(
																	model,
																	query.metric_ids,
																)[0]?.time_role ?? ''),
														metric_roles: enabled
															? Object.fromEntries(
																	atomicMetricDependencies(
																		model,
																		query.metric_ids,
																	).map((metric) => [
																		metric.metric_id,
																		metric.time_role!,
																	]),
																)
															: undefined,
													},
												});
										}}
									/>
									<ul className="text-xs text-secondary">
										{atomicDependencies.map((metric) => (
											<li key={metric.metric_id}>
												{metric.name}: {metric.time_role ?? 'No time field'}
											</li>
										))}
									</ul>
								</div>
							)}
							<TextField
								label="Business time role"
								disabled={Object.keys(query.time?.metric_roles ?? {}).length > 0}
								error={fieldError('time.time_role')}
								value={query.time?.time_role ?? ''}
								hint="Must match selected metrics"
								onChange={(time_role) =>
									patch({
										time: {
											metric_roles: query.time?.metric_roles,
											start: query.time?.start ?? '',
											end_exclusive: query.time?.end_exclusive ?? '',
											timezone: query.time?.timezone ?? 'UTC',
											time_role,
											granularity: query.time?.granularity ?? null,
										},
									})
								}
							/>
							<SelectField
								label="Time grouping"
								value={query.time?.granularity ?? ''}
								options={[
									{ value: '', label: 'No time grouping' },
									...options(['day', 'month', 'year']),
								]}
								onChange={(granularity) =>
									patch({
										time: {
											metric_roles: query.time?.metric_roles,
											start: query.time?.start ?? '',
											end_exclusive: query.time?.end_exclusive ?? '',
											timezone: query.time?.timezone ?? 'UTC',
											time_role:
												query.time?.time_role ??
												selected[0]?.time_role ??
												'',
											granularity: granularity
												? (granularity as NonNullable<
														MetricQueryRequest['time']
													>['granularity'])
												: null,
										},
									})
								}
							/>
						</div>
					)}
					<PredicateEditor
						allowRolePaths
						label="Additional query filters"
						value={query.filters}
						sources={model.sources}
						onChange={(filters) => patch({ filters })}
					/>
					<div className="grid gap-4 md:grid-cols-3">
						<TextField
							label="Row limit (1–10,000)"
							error={fieldError('limit')}
							type="number"
							min={1}
							max={10000}
							step={1}
							value={query.limit}
							onChange={(limit) => patch({ limit: Number(limit) })}
						/>
						<SelectField
							label="Order by"
							value={query.order_by[0]?.field_id ?? ''}
							options={[
								{ value: '', label: 'Default order' },
								...options([...query.metric_ids, ...query.dimensions]),
							]}
							onChange={(field_id) =>
								patch({
									order_by: field_id
										? [
												{
													field_id,
													direction:
														query.order_by[0]?.direction ?? 'asc',
												},
											]
										: [],
								})
							}
						/>
						{query.order_by[0] && (
							<SelectField
								label="Direction"
								value={query.order_by[0].direction}
								options={options(['asc', 'desc'])}
								onChange={(direction) =>
									patch({
										order_by: [
											{
												...query.order_by[0],
												direction: direction as 'asc' | 'desc',
											},
										],
									})
								}
							/>
						)}
					</div>
					<details>
						<summary className="cursor-pointer text-sm font-medium">
							Structured request
						</summary>
						<pre className="mt-3 max-h-80 overflow-auto rounded-lg bg-zinc-50 p-4 text-xs dark:bg-zinc-950">
							{JSON.stringify(query, null, 2)}
						</pre>
					</details>
					<div className="flex flex-wrap gap-3">
						{canEdit && (
							<Button
								theme={ButtonTheme.Secondary}
								onClick={() => void run('compile', true)}
							>
								Preview draft SQL
							</Button>
						)}
						<Button
							theme={ButtonTheme.Secondary}
							disabled={!active}
							onClick={() => void run('compile', false)}
						>
							Compile certified query
						</Button>
						<Button disabled={!active} onClick={() => void run('execute', false)}>
							Execute certified query
						</Button>
					</div>
				</fieldset>
			</section>
			{busy && (
				<p role="status" className="text-sm">
					Processing governed query…
				</p>
			)}
			{fieldErrors.length > 0 && (
				<ul
					aria-label="Query field errors"
					className="list-disc pl-5 text-sm text-red-700 dark:text-red-300"
				>
					{fieldErrors.map((issue, index) => (
						<li key={index}>
							{issue.path}: {issue.message}
						</li>
					))}
				</ul>
			)}
			{error && (
				<p
					role="alert"
					className="rounded-lg bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950 dark:text-red-200"
				>
					{error}
				</p>
			)}
			{result && <QueryResult result={result} executed={executed} />}
		</div>
	);
};
