// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from 'react';
import { Button } from '@/common/Button';
import { ButtonTheme, Size } from '@/enums/button';
import { newMetric, newSource, parseModel, splitIds } from '@/lib/governedMetrics';
import type { GovernedModel, Metric, Relationship, SourceBinding } from '@/types/governedMetrics';
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

const sourceOptions = (model: GovernedModel) => [
	{ value: '', label: 'Select source' },
	...options(model.sources.map((source) => source.source_id)),
];

const SourceEditor = ({
	source,
	model,
	onChange,
	onRemove,
}: {
	source: SourceBinding;
	model: GovernedModel;
	onChange: (source: SourceBinding) => void;
	onRemove: () => void;
}) => {
	const patch = (value: Partial<SourceBinding>) => onChange({ ...source, ...value });
	return (
		<details className={panelClass} open>
			<summary className="cursor-pointer text-base font-semibold">
				Source · {source.source_id || 'Untitled'}
			</summary>
			<div className="mt-4 space-y-5">
				<div className="grid gap-4 md:grid-cols-3">
					<TextField
						label="Source ID"
						value={source.source_id}
						onChange={(source_id) => patch({ source_id })}
					/>
					<TextField
						label="Datasource ID"
						value={source.datasource_id}
						hint="Catalog database ID (not the connection name)"
						onChange={(datasource_id) => patch({ datasource_id })}
					/>
					<TextField
						label="Owner term ID"
						value={source.owner_term_id}
						hint="Business term that defines this source"
						onChange={(owner_term_id) => patch({ owner_term_id })}
					/>
					<TextField
						label="Catalog table ID"
						value={source.catalog_table_id}
						onChange={(catalog_table_id) => patch({ catalog_table_id })}
					/>
					<TextField
						label="Schema"
						value={source.schema_name}
						onChange={(schema_name) => patch({ schema_name })}
					/>
					<TextField
						label="Table"
						value={source.table_name}
						onChange={(table_name) => patch({ table_name })}
					/>
					<TextField
						label="Grain field IDs"
						value={source.grain.join(', ')}
						hint="All columns of the unique key, comma separated"
						onChange={(value) => patch({ grain: splitIds(value) })}
					/>
					<TextField
						label="Priority"
						type="number"
						value={source.priority}
						onChange={(value) => patch({ priority: Number(value) })}
					/>
				</div>
				<fieldset className="space-y-3">
					<legend className="mb-2 font-medium">Catalog field bindings</legend>
					{source.fields.map((field, index) => (
						<div
							key={index}
							className="grid gap-3 rounded-lg bg-zinc-50 p-3 md:grid-cols-4 dark:bg-zinc-950"
						>
							<TextField
								label="Field ID"
								value={field.field_id}
								onChange={(field_id) =>
									patch({
										fields: source.fields.map((item, at) =>
											at === index ? { ...item, field_id } : item,
										),
									})
								}
							/>
							<TextField
								label="Catalog column ID"
								value={field.catalog_column_id}
								onChange={(catalog_column_id) =>
									patch({
										fields: source.fields.map((item, at) =>
											at === index ? { ...item, catalog_column_id } : item,
										),
									})
								}
							/>
							<TextField
								label="Column name"
								value={field.column_name}
								onChange={(column_name) =>
									patch({
										fields: source.fields.map((item, at) =>
											at === index ? { ...item, column_name } : item,
										),
									})
								}
							/>
							<SelectField
								label="Type"
								value={field.data_type}
								options={options([
									'number',
									'string',
									'boolean',
									'date',
									'datetime',
								])}
								onChange={(data_type) =>
									patch({
										fields: source.fields.map((item, at) =>
											at === index
												? {
														...item,
														data_type:
															data_type as typeof field.data_type,
													}
												: item,
										),
									})
								}
							/>
							<CheckField
								label="Nullable"
								checked={field.nullable}
								onChange={(nullable) =>
									patch({
										fields: source.fields.map((item, at) =>
											at === index ? { ...item, nullable } : item,
										),
									})
								}
							/>
							<Button
								theme={ButtonTheme.Minimal}
								size={Size.SMALL}
								onClick={() =>
									patch({ fields: source.fields.filter((_, at) => at !== index) })
								}
							>
								Remove field
							</Button>
						</div>
					))}
					<Button
						theme={ButtonTheme.Secondary}
						size={Size.SMALL}
						onClick={() =>
							patch({
								fields: [
									...source.fields,
									{
										field_id: `field_${source.fields.length + 1}`,
										catalog_column_id: '',
										column_name: '',
										data_type: 'string',
										nullable: true,
									},
								],
							})
						}
					>
						Add field binding
					</Button>
				</fieldset>
				<fieldset className="space-y-3">
					<legend className="mb-2 font-medium">
						Dimensions reachable from this source
					</legend>
					{source.dimensions.map((dimension, index) => {
						const update = (value: Partial<typeof dimension>) =>
							patch({
								dimensions: source.dimensions.map((item, at) =>
									at === index ? { ...item, ...value } : item,
								),
							});
						return (
							<div
								key={index}
								className="grid gap-3 rounded-lg bg-zinc-50 p-3 md:grid-cols-4 dark:bg-zinc-950"
							>
								<TextField
									label="Dimension ID"
									value={dimension.dimension_id}
									onChange={(dimension_id) => update({ dimension_id })}
								/>
								<SelectField
									label="Dimension source"
									value={dimension.field.source_id}
									options={sourceOptions(model)}
									onChange={(source_id) =>
										update({ field: { source_id, field_id: '' } })
									}
								/>
								<SelectField
									label="Dimension field"
									value={dimension.field.field_id}
									options={[
										{ value: '', label: 'Select field' },
										...options(
											model.sources
												.find(
													(item) =>
														item.source_id ===
														dimension.field.source_id,
												)
												?.fields.map((item) => item.field_id) ?? [],
										),
									]}
									onChange={(field_id) =>
										update({ field: { ...dimension.field, field_id } })
									}
								/>
								<TextField
									label="Relationship path"
									value={dimension.relationship_path.join(', ')}
									hint="Ordered relationship IDs, empty for a local field"
									onChange={(value) =>
										update({ relationship_path: splitIds(value) })
									}
								/>
								<Button
									theme={ButtonTheme.Minimal}
									size={Size.SMALL}
									onClick={() =>
										patch({
											dimensions: source.dimensions.filter(
												(_, at) => at !== index,
											),
										})
									}
								>
									Remove dimension
								</Button>
							</div>
						);
					})}
					<Button
						theme={ButtonTheme.Secondary}
						size={Size.SMALL}
						onClick={() =>
							patch({
								dimensions: [
									...source.dimensions,
									{
										dimension_id: `dimension_${source.dimensions.length + 1}`,
										field: { source_id: source.source_id, field_id: '' },
										relationship_path: [],
									},
								],
							})
						}
					>
						Add dimension
					</Button>
				</fieldset>
				<PredicateEditor
					label="Required source filters"
					value={source.required_filters}
					sources={model.sources}
					onChange={(required_filters) => patch({ required_filters })}
				/>
				<Button theme={ButtonTheme.DangerSubtle} size={Size.SMALL} onClick={onRemove}>
					Remove source
				</Button>
			</div>
		</details>
	);
};

const RelationshipEditor = ({
	relationship,
	model,
	onChange,
	onRemove,
}: {
	relationship: Relationship;
	model: GovernedModel;
	onChange: (relationship: Relationship) => void;
	onRemove: () => void;
}) => {
	const patch = (value: Partial<Relationship>) => onChange({ ...relationship, ...value });
	return (
		<details className={panelClass} open>
			<summary className="cursor-pointer text-base font-semibold">
				Relationship · {relationship.relationship_id}
			</summary>
			<div className="mt-4 space-y-4">
				<div className="grid gap-4 md:grid-cols-3">
					<TextField
						label="Relationship ID"
						value={relationship.relationship_id}
						onChange={(relationship_id) => patch({ relationship_id })}
					/>
					<SelectField
						label="From source"
						value={relationship.source_id}
						options={sourceOptions(model)}
						onChange={(source_id) => patch({ source_id })}
					/>
					<SelectField
						label="To source"
						value={relationship.target_id}
						options={sourceOptions(model)}
						onChange={(target_id) => patch({ target_id })}
					/>
					<TextField
						label="Business role"
						value={relationship.role}
						onChange={(role) => patch({ role })}
					/>
					<SelectField
						label="Cardinality"
						value={relationship.cardinality}
						options={options([
							'many_to_one',
							'one_to_one',
							'one_to_many',
							'many_to_many',
						])}
						hint="Fan-out and many-to-many paths are rejected by the compiler."
						onChange={(cardinality) =>
							patch({ cardinality: cardinality as Relationship['cardinality'] })
						}
					/>
					<SelectField
						label="Join type"
						value={relationship.join_type}
						options={options(['left', 'inner'])}
						onChange={(join_type) =>
							patch({ join_type: join_type as Relationship['join_type'] })
						}
					/>
				</div>
				<fieldset className="space-y-3">
					<legend className="mb-2 text-sm font-medium">
						Join keys · include every part of a composite key
					</legend>
					{relationship.keys.map((key, index) => (
						<div key={index} className="grid gap-3 md:grid-cols-3">
							<SelectField
								label="From field"
								value={key.source_field_id}
								options={[
									{ value: '', label: 'Select field' },
									...options(
										model.sources
											.find(
												(item) => item.source_id === relationship.source_id,
											)
											?.fields.map((item) => item.field_id) ?? [],
									),
								]}
								onChange={(source_field_id) =>
									patch({
										keys: relationship.keys.map((item, at) =>
											at === index ? { ...item, source_field_id } : item,
										),
									})
								}
							/>
							<SelectField
								label="To field"
								value={key.target_field_id}
								options={[
									{ value: '', label: 'Select field' },
									...options(
										model.sources
											.find(
												(item) => item.source_id === relationship.target_id,
											)
											?.fields.map((item) => item.field_id) ?? [],
									),
								]}
								onChange={(target_field_id) =>
									patch({
										keys: relationship.keys.map((item, at) =>
											at === index ? { ...item, target_field_id } : item,
										),
									})
								}
							/>
							<Button
								theme={ButtonTheme.Minimal}
								onClick={() =>
									patch({
										keys: relationship.keys.filter((_, at) => at !== index),
									})
								}
							>
								Remove key
							</Button>
						</div>
					))}
					<Button
						theme={ButtonTheme.Secondary}
						size={Size.SMALL}
						onClick={() =>
							patch({
								keys: [
									...relationship.keys,
									{ source_field_id: '', target_field_id: '' },
								],
							})
						}
					>
						Add join key
					</Button>
				</fieldset>
				<PredicateEditor
					label="Join predicates"
					value={relationship.predicates}
					sources={model.sources}
					onChange={(predicates) => patch({ predicates })}
				/>
				<Button theme={ButtonTheme.DangerSubtle} size={Size.SMALL} onClick={onRemove}>
					Remove relationship
				</Button>
			</div>
		</details>
	);
};

const MetricEditor = ({
	metric,
	model,
	onChange,
	onRemove,
}: {
	metric: Metric;
	model: GovernedModel;
	onChange: (metric: Metric) => void;
	onRemove: () => void;
}) => {
	const patch = (value: Partial<Metric>) => onChange({ ...metric, ...value });
	const source = model.sources.find((item) => item.source_id === metric.source_ids[0]);
	const binary = metric.expression?.kind === 'binary' ? metric.expression : null;
	const simpleComposite = binary?.left.kind === 'metric' && binary.right.kind === 'metric';
	return (
		<details className={panelClass} open>
			<summary className="cursor-pointer text-base font-semibold">
				Metric · {metric.name || metric.metric_id}
			</summary>
			<div className="mt-4 space-y-5">
				<div className="grid gap-4 md:grid-cols-3">
					<TextField
						label="Metric ID"
						value={metric.metric_id}
						hint="Stable identity used by saved queries"
						onChange={(metric_id) => patch({ metric_id })}
					/>
					<TextField
						label="Display name"
						value={metric.name}
						onChange={(name) => patch({ name })}
					/>
					<TextField
						label="Owner term ID"
						value={metric.owner_term_id}
						onChange={(owner_term_id) => patch({ owner_term_id })}
					/>
					<TextField
						label="Definition / business meaning"
						value={metric.description}
						onChange={(description) => patch({ description })}
					/>
					<SelectField
						label="Kind"
						value={metric.kind}
						options={options(['atomic', 'composite'])}
						onChange={(kind) =>
							patch(
								kind === 'atomic'
									? { kind, aggregation: 'count', expression: null }
									: {
											kind: 'composite',
											aggregation: null,
											expression: {
												kind: 'binary',
												operator: 'divide',
												left: { kind: 'metric', metric_id: '' },
												right: { kind: 'metric', metric_id: '' },
											},
										},
							)
						}
					/>
					<TextField
						label="Unit"
						value={metric.unit}
						onChange={(unit) => patch({ unit })}
					/>
					<TextField
						label="Family ID"
						value={metric.family_id ?? ''}
						hint="Group related definitions; variants remain explicit"
						onChange={(family_id) => patch({ family_id: family_id || null })}
					/>
					<TextField
						label="Fixed currency"
						value={metric.fixed_currency ?? ''}
						hint="E.g. CNY; never an implicit conversion"
						onChange={(fixed_currency) =>
							patch({ fixed_currency: fixed_currency || null })
						}
					/>
					<SelectField
						label="Currency field"
						value={metric.currency_field_id ?? ''}
						options={[
							{ value: '', label: 'None' },
							...options(source?.fields.map((field) => field.field_id) ?? []),
						]}
						onChange={(currency_field_id) =>
							patch({ currency_field_id: currency_field_id || null })
						}
					/>
				</div>
				<ChoiceSet
					label="Eligible sources"
					options={model.sources.map((item) => item.source_id)}
					value={metric.source_ids}
					onChange={(source_ids) => patch({ source_ids })}
				/>
				{metric.kind === 'atomic' ? (
					<div className="grid gap-4 md:grid-cols-2">
						<SelectField
							label="Aggregation"
							value={metric.aggregation ?? ''}
							options={options(['sum', 'count', 'count_distinct'])}
							onChange={(aggregation) =>
								patch({ aggregation: aggregation as Metric['aggregation'] })
							}
						/>
						{!metric.expression || metric.expression.kind === 'field' ? (
							<SelectField
								label="Value field"
								value={
									metric.expression?.kind === 'field'
										? metric.expression.field_id
										: ''
								}
								options={[
									{ value: '', label: 'Count rows (no field)' },
									...options(source?.fields.map((field) => field.field_id) ?? []),
								]}
								onChange={(field_id) =>
									patch({
										expression: field_id ? { kind: 'field', field_id } : null,
									})
								}
							/>
						) : (
							<p className="text-sm text-secondary">
								A typed expression is configured. Edit its expression tree in
								Advanced JSON.
							</p>
						)}
					</div>
				) : simpleComposite && binary ? (
					<div className="grid gap-4 md:grid-cols-3">
						<SelectField
							label="Left metric"
							value={binary.left.kind === 'metric' ? binary.left.metric_id : ''}
							options={[
								{ value: '', label: 'Select metric' },
								...options(
									model.metrics
										.filter((item) => item.metric_id !== metric.metric_id)
										.map((item) => item.metric_id),
								),
							]}
							onChange={(metric_id) =>
								patch({
									expression: { ...binary, left: { kind: 'metric', metric_id } },
								})
							}
						/>
						<SelectField
							label="Operator"
							value={binary.operator}
							options={options(['add', 'subtract', 'multiply', 'divide'])}
							onChange={(operator) =>
								patch({
									expression: {
										...binary,
										operator: operator as typeof binary.operator,
									},
								})
							}
						/>
						<SelectField
							label="Right metric"
							value={binary.right.kind === 'metric' ? binary.right.metric_id : ''}
							options={[
								{ value: '', label: 'Select metric' },
								...options(
									model.metrics
										.filter((item) => item.metric_id !== metric.metric_id)
										.map((item) => item.metric_id),
								),
							]}
							onChange={(metric_id) =>
								patch({
									expression: { ...binary, right: { kind: 'metric', metric_id } },
								})
							}
						/>
					</div>
				) : (
					<p className="text-sm text-secondary">
						Edit this composite expression tree in Advanced JSON.
					</p>
				)}
				<ChoiceSet
					label="Supported dimensions"
					options={Array.from(
						new Set(
							model.sources.flatMap((item) =>
								item.dimensions.map((dimension) => dimension.dimension_id),
							),
						),
					)}
					value={metric.supported_dimensions}
					onChange={(supported_dimensions) => patch({ supported_dimensions })}
				/>
				<div className="grid gap-4 md:grid-cols-3">
					<SelectField
						label="Time field"
						value={metric.time_field_id ?? ''}
						options={[
							{ value: '', label: 'None' },
							...options(
								source?.fields
									.filter((field) =>
										['date', 'datetime'].includes(field.data_type),
									)
									.map((field) => field.field_id) ?? [],
							),
						]}
						onChange={(time_field_id) =>
							patch({ time_field_id: time_field_id || null })
						}
					/>
					<TextField
						label="Time role"
						value={metric.time_role ?? ''}
						hint="E.g. order_created; confirmation is not payment"
						onChange={(time_role) => patch({ time_role: time_role || null })}
					/>
					<SelectField
						label="Empty result"
						value={metric.empty_value}
						options={options(['null', 'zero'])}
						onChange={(empty_value) =>
							patch({ empty_value: empty_value as Metric['empty_value'] })
						}
					/>
					<SelectField
						label="Division by zero"
						value={metric.zero_division}
						options={options(['null', 'error'])}
						onChange={(zero_division) =>
							patch({ zero_division: zero_division as Metric['zero_division'] })
						}
					/>
				</div>
				<ChoiceSet
					label="Time granularities"
					options={['day', 'month', 'year']}
					value={metric.supported_granularities}
					onChange={(value) =>
						patch({
							supported_granularities: value as Metric['supported_granularities'],
						})
					}
				/>
				<CheckField
					label="Default variant of family"
					checked={metric.default_of_family}
					onChange={(default_of_family) => patch({ default_of_family })}
				/>
				<PredicateEditor
					label="Required metric filters"
					value={metric.required_filters}
					sources={model.sources}
					onChange={(required_filters) => patch({ required_filters })}
				/>
				<Field
					label="Definition evidence"
					hint="One source, business rule, or explanation per line"
				>
					<textarea
						className={inputClass}
						rows={3}
						value={metric.evidence.join('\n')}
						onChange={(event) => patch({ evidence: event.target.value.split('\n') })}
					/>
				</Field>
				<Button theme={ButtonTheme.DangerSubtle} size={Size.SMALL} onClick={onRemove}>
					Remove metric
				</Button>
			</div>
		</details>
	);
};

export const ModelEditor = ({
	model,
	onChange,
	disabled,
	onPendingChange,
}: {
	model: GovernedModel;
	onChange: (model: GovernedModel) => void;
	disabled: boolean;
	onPendingChange: (pending: boolean) => void;
}) => {
	const [section, setSection] = useState('metrics');
	const [advanced, setAdvanced] = useState<string | null>(null);
	const [error, setError] = useState('');
	return (
		<div className="space-y-5">
			<fieldset
				disabled={disabled || advanced !== null}
				className="space-y-5 disabled:opacity-70"
			>
				<div className="grid gap-4 md:grid-cols-2">
					<TextField
						label="Model name"
						value={model.name}
						onChange={(name) => onChange({ ...model, name })}
					/>
					<TextField
						label="Model ID"
						value={model.model_id}
						disabled
						onChange={() => {}}
					/>
				</div>
				<div className="flex flex-wrap gap-2" aria-label="Model sections">
					{[
						['metrics', `Metrics (${model.metrics.length})`],
						['sources', `Sources (${model.sources.length})`],
						['relationships', `Relationships (${model.relationships.length})`],
					].map(([key, label]) => (
						<Button
							key={key}
							theme={section === key ? ButtonTheme.Primary : ButtonTheme.Secondary}
							onClick={() => setSection(key)}
							aria-pressed={section === key}
						>
							{label}
						</Button>
					))}
				</div>
				{section === 'sources' && (
					<div className="space-y-4">
						<p className="text-sm text-secondary">
							Bind every field to an ingested Catalog column. Save and validate to
							check bindings, types, grain, and safe relationship paths.
						</p>
						{model.sources.map((source, index) => (
							<SourceEditor
								key={index}
								source={source}
								model={model}
								onChange={(value) =>
									onChange({
										...model,
										sources: model.sources.map((item, at) =>
											at === index ? value : item,
										),
									})
								}
								onRemove={() =>
									onChange({
										...model,
										sources: model.sources.filter((_, at) => at !== index),
									})
								}
							/>
						))}
						<Button
							theme={ButtonTheme.Secondary}
							onClick={() =>
								onChange({
									...model,
									sources: [
										...model.sources,
										newSource(model.sources.length + 1),
									],
								})
							}
						>
							Add source
						</Button>
					</div>
				)}
				{section === 'relationships' && (
					<div className="space-y-4">
						{model.relationships.map((relationship, index) => (
							<RelationshipEditor
								key={index}
								relationship={relationship}
								model={model}
								onChange={(value) =>
									onChange({
										...model,
										relationships: model.relationships.map((item, at) =>
											at === index ? value : item,
										),
									})
								}
								onRemove={() =>
									onChange({
										...model,
										relationships: model.relationships.filter(
											(_, at) => at !== index,
										),
									})
								}
							/>
						))}
						<Button
							theme={ButtonTheme.Secondary}
							onClick={() =>
								onChange({
									...model,
									relationships: [
										...model.relationships,
										{
											relationship_id: `relationship_${model.relationships.length + 1}`,
											source_id: '',
											target_id: '',
											role: '',
											keys: [{ source_field_id: '', target_field_id: '' }],
											cardinality: 'many_to_one',
											join_type: 'left',
											predicates: [],
										},
									],
								})
							}
						>
							Add relationship
						</Button>
					</div>
				)}
				{section === 'metrics' && (
					<div className="space-y-4">
						{model.metrics.map((metric, index) => (
							<MetricEditor
								key={index}
								metric={metric}
								model={model}
								onChange={(value) =>
									onChange({
										...model,
										metrics: model.metrics.map((item, at) =>
											at === index ? value : item,
										),
									})
								}
								onRemove={() =>
									onChange({
										...model,
										metrics: model.metrics.filter((_, at) => at !== index),
									})
								}
							/>
						))}
						<Button
							theme={ButtonTheme.Secondary}
							onClick={() =>
								onChange({
									...model,
									metrics: [
										...model.metrics,
										newMetric(
											model.metrics.length + 1,
											model.sources[0]?.source_id,
										),
									],
								})
							}
						>
							Add metric
						</Button>
					</div>
				)}
			</fieldset>
			<details className={panelClass}>
				<summary className="cursor-pointer font-medium">
					Advanced JSON · typed expressions and full contract
				</summary>
				<div className="mt-4 space-y-3">
					<p className="text-sm text-secondary">
						Apply JSON to the form, then save a new draft. Editing JSON never certifies
						or activates a model.
					</p>
					<textarea
						aria-label="Model JSON"
						className={`${inputClass} font-mono text-xs`}
						rows={18}
						spellCheck={false}
						value={advanced ?? JSON.stringify(model, null, 2)}
						disabled={disabled}
						onChange={(event) => {
							setAdvanced(event.target.value);
							onPendingChange(true);
							setError('');
						}}
					/>
					{error && (
						<p role="alert" className="text-sm whitespace-pre-wrap text-red-600">
							{error}
						</p>
					)}
					<div className="flex gap-2">
						<Button
							disabled={disabled || advanced === null}
							onClick={() => {
								try {
									const parsed = parseModel(advanced ?? '');
									if (parsed.model_id !== model.model_id)
										throw new Error(
											'Model ID cannot change in an existing editor. Import as a new draft instead.',
										);
									onChange(parsed);
									setAdvanced(null);
									onPendingChange(false);
									setError('');
								} catch (error) {
									setError(
										error instanceof Error
											? error.message
											: 'Invalid model JSON',
									);
								}
							}}
						>
							Apply JSON to form
						</Button>
						<Button
							theme={ButtonTheme.Secondary}
							disabled={advanced === null}
							onClick={() => {
								setAdvanced(null);
								onPendingChange(false);
								setError('');
							}}
						>
							Discard JSON edits
						</Button>
					</div>
				</div>
			</details>
		</div>
	);
};
