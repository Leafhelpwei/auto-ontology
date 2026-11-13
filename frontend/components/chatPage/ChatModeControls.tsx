// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ChatSelection } from '@/types/chat';
import type { useMetricChatModels } from '@/lib/useMetricChatModels';
import { Button } from '@/common/Button';
import { ButtonTheme, Size } from '@/enums/button';
import { CheckField, SelectField } from '@/components/metricsPage/MetricFields';

export const ChatModeControls = ({
	selection,
	onChange,
	metrics,
	disabled,
	canReadMetrics,
}: {
	selection: ChatSelection;
	onChange: (selection: ChatSelection) => void;
	metrics: ReturnType<typeof useMetricChatModels>;
	disabled: boolean;
	canReadMetrics: boolean;
}) => {
	const context = selection.metric_context;
	const missingChoice =
		context && !metrics.activeModels.some((model) => model.model_id === context.model_id);
	return (
		<section
			className="border-b border-zinc-200 px-4 py-3 dark:border-zinc-700"
			aria-label="Chat query mode"
		>
			<div className="mx-auto max-w-3xl space-y-3">
				<div className="flex flex-wrap items-center gap-2">
					<Button
						theme={
							selection.query_mode === 'explore'
								? ButtonTheme.Primary
								: ButtonTheme.Secondary
						}
						size={Size.SMALL}
						disabled={disabled}
						aria-pressed={selection.query_mode === 'explore'}
						onClick={() => onChange({ query_mode: 'explore', metric_context: null })}
					>
						Explore data
					</Button>
					<Button
						theme={
							selection.query_mode === 'metrics'
								? ButtonTheme.Primary
								: ButtonTheme.Secondary
						}
						size={Size.SMALL}
						disabled={disabled || !canReadMetrics}
						aria-pressed={selection.query_mode === 'metrics'}
						onClick={() => onChange({ query_mode: 'metrics', metric_context: null })}
					>
						Standard metrics
					</Button>
				</div>
				{selection.query_mode === 'metrics' && (
					<div className="space-y-3">
						<SelectField
							label="Activated metric model"
							value={context?.model_id ?? ''}
							disabled={disabled || metrics.loading}
							options={[
								{ value: '', label: 'Select a model' },
								...(missingChoice
									? [
											{
												value: context.model_id,
												label: `${context.model_id} · saved selection`,
											},
										]
									: []),
								...metrics.activeModels.map((model) => ({
									value: model.model_id,
									label: `${model.name} · active r${model.active_revision}`,
								})),
							]}
							onChange={(modelId) => {
								const model = metrics.activeModels.find(
									(item) => item.model_id === modelId,
								);
								onChange({
									query_mode: 'metrics',
									metric_selection_explicit: true,
									metric_context:
										model && model.active_revision != null
											? {
													model_id: model.model_id,
													revision: model.active_revision,
													selected_metric_ids: [],
												}
											: null,
								});
							}}
						/>
						{metrics.unavailable && (
							<p
								role={metrics.error ? 'alert' : 'status'}
								className={`text-xs ${metrics.error ? 'text-red-700 dark:text-red-300' : 'text-secondary'}`}
							>
								{metrics.unavailable}
							</p>
						)}
						{metrics.error && (
							<Button
								theme={ButtonTheme.Secondary}
								size={Size.SMALL}
								disabled={disabled}
								onClick={() => {
									void metrics.models.refetch();
									void metrics.config.refetch();
									if (context?.model_id) void metrics.detail.refetch();
								}}
							>
								Retry loading models
							</Button>
						)}
						{!metrics.loading &&
							!metrics.error &&
							metrics.activeModels.length === 0 && (
								<p className="text-xs text-secondary">
									No activated models are available. Validate, certify, and
									activate a model in Metrics first.
								</p>
							)}
						{metrics.staleRevision && metrics.detail.data?.active_revision != null && (
							<Button
								theme={ButtonTheme.Secondary}
								size={Size.SMALL}
								disabled={disabled}
								onClick={() =>
									onChange({
										query_mode: 'metrics',
										metric_selection_explicit: true,
										metric_context: {
											model_id: metrics.detail.data!.model_id,
											revision: metrics.detail.data!.active_revision!,
											selected_metric_ids: [],
										},
									})
								}
							>
								Use current active revision {metrics.detail.data.active_revision} ·
								reset metric context
							</Button>
						)}
						{metrics.revision && context && (
							<details>
								<summary className="cursor-pointer text-xs font-medium">
									Metric variants (optional) · revision{' '}
									{metrics.revision.revision}
									{(context.selected_metric_ids ?? []).length
										? ` · ${(context.selected_metric_ids ?? []).join(', ')}`
										: ''}
								</summary>
								<fieldset
									disabled={disabled}
									className="mt-3 grid gap-3 sm:grid-cols-2"
								>
									{metrics.revision.payload.metrics.map((metric) => (
										<div key={metric.metric_id}>
											<CheckField
												label={`${metric.name} (${metric.metric_id})`}
												checked={(
													context.selected_metric_ids ?? []
												).includes(metric.metric_id)}
												onChange={(checked) =>
													onChange({
														query_mode: 'metrics',
														metric_selection_explicit: true,
														metric_context: {
															...context,
															selected_metric_ids: checked
																? [
																		...(context.selected_metric_ids ??
																			[]),
																		metric.metric_id,
																	]
																: (
																		context.selected_metric_ids ??
																		[]
																	).filter(
																		(id) =>
																			id !== metric.metric_id,
																	),
														},
													})
												}
											/>
											<p className="mt-1 pl-6 text-xs text-secondary">
												{metric.description}
											</p>
										</div>
									))}
								</fieldset>
								<p className="mt-2 text-xs text-secondary">
									Leave variants unselected to let the interpreter identify them.
									Ambiguous families require clarification.
								</p>
							</details>
						)}
						<p className="text-xs text-secondary">
							Standard metrics use certified definitions. A rejected or ambiguous
							request stays in this mode.
						</p>
					</div>
				)}
			</div>
		</section>
	);
};
