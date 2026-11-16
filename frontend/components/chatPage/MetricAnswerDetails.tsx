// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { MetricsQueryContext } from '@/types/chat';
import { metricResultTable } from '@/lib/chatMetricContext';
import { Button } from '@/common/Button';
import { ButtonTheme, Size } from '@/enums/button';
import { ResultColumnDetails } from '@/components/metricsPage/ResultColumnDetails';
import { DynamicTable } from './DynamicTable';

export const MetricAnswerDetails = ({
	context,
	sqlResponse,
	onSelectMetric,
	onConfirmQuery,
	onRestartQuery,
	disabled = false,
}: {
	context: MetricsQueryContext;
	sqlResponse?: string;
	onSelectMetric?: (context: MetricsQueryContext, metricId: string) => void;
	onConfirmQuery?: (context: MetricsQueryContext) => void;
	onRestartQuery?: (context: MetricsQueryContext) => void;
	disabled?: boolean;
}) => {
	const table = metricResultTable(sqlResponse, context);
	const executed = context.status === 'ready' && context.trusted === true;
	const pending = context.status === 'confirmation_required' ? context.pending_query : null;
	const status = executed
		? 'Certified metric execution'
		: pending
			? 'Review query · no query executed'
			: context.status === 'clarification'
				? 'Clarification required · no query executed'
				: context.status === 'rejected'
					? 'Request rejected · no query executed'
					: context.status === 'cancelled'
						? 'Metric query cancelled'
						: context.status === 'error'
							? 'Metric query failed'
							: context.status === 'running'
								? 'Metric query in progress'
								: 'Standard metrics';
	return (
		<section
			className="mt-3 space-y-3 border-t border-zinc-300 pt-3 dark:border-zinc-600"
			aria-label="Metric query context"
		>
			<div className="flex flex-wrap items-center gap-2">
				<span
					className={`rounded-full px-2 py-1 text-xs font-medium ${executed ? 'bg-[#76b900]/15 text-[#4d7a00] dark:text-[#a3d63a]' : 'bg-zinc-200 text-body dark:bg-zinc-700 dark:text-zinc-200'}`}
				>
					{status}
				</span>
				<span className="text-xs text-secondary">
					{context.model_id}
					{context.revision != null ? ` · revision ${context.revision}` : ''}
				</span>
			</div>
			{(context.selected_metric_ids ?? []).length > 0 && (
				<p className="text-xs text-secondary">
					Metrics: {(context.selected_metric_ids ?? []).join(', ')}
				</p>
			)}
			{executed && (
				<p className="text-xs text-secondary">
					{context.intent_confirmed
						? 'Query scope confirmed by you.'
						: 'Execution is certified; intent confirmation was not recorded.'}
				</p>
			)}
			{context.clarification?.code === 'CONTEXT_TOO_LARGE' && (
				<Button
					size={Size.SMALL}
					disabled={disabled || !onRestartQuery}
					onClick={() => onRestartQuery?.(context)}
				>
					Restate the complete question
				</Button>
			)}
			{pending && (
				<div className="space-y-3 rounded-lg border border-zinc-300 bg-white p-3 text-sm dark:border-zinc-600 dark:bg-zinc-900">
					<h3 className="font-medium">Review the meaning before execution</h3>
					<dl className="space-y-2">
						<div>
							<dt className="font-medium">Metrics</dt>
							<dd>
								{pending.metric_ids
									.map((id) => `${context.metric_names?.[id] ?? id} (${id})`)
									.join(', ')}
							</dd>
						</div>
						<div>
							<dt className="font-medium">Period</dt>
							<dd>
								{pending.all_time
									? 'All history'
									: `${pending.time?.start} ≤ time < ${pending.time?.end_exclusive}`}
							</dd>
						</div>
						{pending.time && (
							<div>
								<dt className="font-medium">Timezone and business time</dt>
								<dd>
									{pending.time.timezone} ·{' '}
									{pending.time.metric_roles &&
									Object.keys(pending.time.metric_roles).length
										? Object.entries(pending.time.metric_roles)
												.map(([id, role]) => `${id}: ${role}`)
												.join(', ')
										: pending.time.time_role}{' '}
									· {pending.time.granularity ?? 'No time grouping'}
								</dd>
							</div>
						)}
						<div>
							<dt className="font-medium">Group by</dt>
							<dd>{pending.dimensions.join(', ') || 'No dimensions'}</dd>
						</div>
						<div>
							<dt className="font-medium">Additional filters and exclusions</dt>
							<dd>
								{pending.filters.length ? (
									<ul className="list-inside list-disc">
										{pending.filters.map((filter, index) => (
											<li key={index}>
												{filter.field.source_id}.{filter.field.field_id}
												{filter.relationship_path?.length
													? ` [${filter.relationship_path.join(' → ')}]`
													: ''}{' '}
												{filter.operator} {JSON.stringify(filter.value)}
											</li>
										))}
									</ul>
								) : (
									'None — only the certified definition’s fixed conditions apply'
								)}
							</dd>
						</div>
						<div>
							<dt className="font-medium">Ordering and row limit</dt>
							<dd>
								{pending.order_by
									.map((item) => `${item.field_id} ${item.direction}`)
									.join(', ') || 'Default ordering'}{' '}
								· {pending.limit} rows
							</dd>
						</div>
					</dl>
					<p className="text-xs text-secondary">
						If a date, metric, currency or exclusion is missing, reply with the
						correction. Confirmation executes exactly this request.
					</p>
					<Button
						size={Size.SMALL}
						disabled={disabled || !onConfirmQuery}
						onClick={() => onConfirmQuery?.(context)}
					>
						Confirm and execute
					</Button>
				</div>
			)}
			{context.status === 'clarification' && (
				<div className="space-y-2">
					<div className="flex flex-wrap gap-2">
						{context.clarification?.options.map((option) => (
							<Button
								key={option.metric_id}
								theme={ButtonTheme.Secondary}
								size={Size.SMALL}
								disabled={disabled || !onSelectMetric}
								title={option.description}
								onClick={() => onSelectMetric?.(context, option.metric_id)}
							>
								{option.name} · {option.metric_id}
							</Button>
						))}
					</div>
					<p className="text-xs text-secondary">
						Choose a variant if needed, then reply with the missing time range,
						dimension, or currency in this conversation.
					</p>
				</div>
			)}
			{sqlResponse != null && (
				<div className="space-y-2">
					<h3 className="text-xs font-medium">Metric results</h3>
					{table ? (
						table.rows.length > 0 ? (
							<DynamicTable table={table} />
						) : (
							<div className="overflow-x-auto rounded-lg border border-zinc-300 bg-white dark:border-zinc-600 dark:bg-zinc-900">
								<table className="w-full text-left text-xs">
									<thead>
										<tr>
											{table.columns.map((column) => (
												<th
													key={column}
													className="border-b border-zinc-200 px-3 py-2 font-medium dark:border-zinc-700"
												>
													{column}
												</th>
											))}
										</tr>
									</thead>
								</table>
								<p className="p-3 text-xs text-secondary">
									0 rows matched. Column definitions are preserved.
								</p>
							</div>
						)
					) : (
						<div>
							<p className="text-xs text-red-700 dark:text-red-300">
								The result table could not be decoded. Original response:
							</p>
							<pre className="mt-2 overflow-auto text-xs">{sqlResponse}</pre>
						</div>
					)}
					{context.column_metadata?.some(
						(column) => column.json_encoding !== 'native',
					) && (
						<p className="text-xs text-secondary">
							Decimal and large integer values are displayed exactly as returned.
						</p>
					)}
				</div>
			)}
			<ResultColumnDetails columns={context.column_metadata} />
			{(context.plan || context.query || context.model_fingerprint) && (
				<details>
					<summary className="cursor-pointer text-xs font-medium">
						Definition, query and execution evidence
					</summary>
					<pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-white p-3 text-xs dark:bg-zinc-900">
						{JSON.stringify(
							{
								query: context.query,
								model_fingerprint: context.model_fingerprint,
								plan: context.plan,
								columns: context.column_metadata,
							},
							null,
							2,
						)}
					</pre>
				</details>
			)}
		</section>
	);
};
