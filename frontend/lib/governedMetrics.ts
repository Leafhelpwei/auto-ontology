// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { z } from 'zod';
import type { Expression, GovernedModel, Metric, SourceBinding } from '@/types/governedMetrics';

const scalar = z.union([z.string(), z.number().finite(), z.boolean(), z.null()]);
const fieldRef = z.object({ source_id: z.string(), field_id: z.string() }).strict();
const predicate = z
	.object({
		field: fieldRef,
		operator: z.enum([
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
		]),
		value: z.union([scalar, z.array(scalar)]).default(null),
	})
	.strict();
const expression: z.ZodType<Expression> = z.lazy(() =>
	z.discriminatedUnion('kind', [
		z.object({ kind: z.literal('field'), field_id: z.string() }).strict(),
		z
			.object({
				kind: z.literal('number'),
				value: z.union([z.string(), z.number().finite()]),
			})
			.strict(),
		z.object({ kind: z.literal('metric'), metric_id: z.string() }).strict(),
		z
			.object({
				kind: z.literal('binary'),
				operator: z.enum(['add', 'subtract', 'multiply', 'divide']),
				left: expression,
				right: expression,
			})
			.strict(),
	]),
);

/** Checks shape before an imported document reaches the form. The server alone validates semantics and Catalog bindings. */
const modelSchema: z.ZodType<GovernedModel> = z
	.object({
		model_id: z.string(),
		name: z.string(),
		dialect: z.literal('postgres').default('postgres'),
		sources: z.array(
			z
				.object({
					source_id: z.string(),
					datasource_id: z.string(),
					catalog_table_id: z.string(),
					owner_term_id: z.string(),
					schema_name: z.string(),
					table_name: z.string(),
					grain: z.array(z.string()),
					fields: z.array(
						z
							.object({
								field_id: z.string(),
								catalog_column_id: z.string(),
								column_name: z.string(),
								data_type: z.enum([
									'number',
									'string',
									'boolean',
									'date',
									'datetime',
								]),
								nullable: z.boolean().default(true),
							})
							.strict(),
					),
					dimensions: z
						.array(
							z
								.object({
									dimension_id: z.string(),
									field: fieldRef,
									relationship_path: z.array(z.string()).default([]),
								})
								.strict(),
						)
						.default([]),
					required_filters: z.array(predicate).default([]),
					priority: z.number().int().default(100),
					pre_aggregated: z.boolean().default(false),
					structure_fingerprint: z.string().default(''),
				})
				.strict(),
		),
		relationships: z
			.array(
				z
					.object({
						relationship_id: z.string(),
						source_id: z.string(),
						target_id: z.string(),
						role: z.string(),
						keys: z.array(
							z
								.object({
									source_field_id: z.string(),
									target_field_id: z.string(),
								})
								.strict(),
						),
						cardinality: z.enum([
							'many_to_one',
							'one_to_one',
							'one_to_many',
							'many_to_many',
						]),
						join_type: z.enum(['inner', 'left']).default('left'),
						predicates: z.array(predicate).default([]),
					})
					.strict(),
			)
			.default([]),
		metrics: z.array(
			z
				.object({
					metric_id: z.string(),
					name: z.string(),
					description: z.string(),
					owner_term_id: z.string(),
					kind: z.enum(['atomic', 'composite']).default('atomic'),
					source_ids: z.array(z.string()),
					aggregation: z
						.enum(['sum', 'count', 'count_distinct'])
						.nullable()
						.default(null),
					expression: expression.nullable().default(null),
					required_filters: z.array(predicate).default([]),
					supported_dimensions: z.array(z.string()).default([]),
					time_field_id: z.string().nullable().default(null),
					time_role: z.string().nullable().default(null),
					supported_granularities: z.array(z.enum(['day', 'month', 'year'])).default([]),
					unit: z.string().default('number'),
					currency_field_id: z.string().nullable().default(null),
					fixed_currency: z.string().nullable().default(null),
					empty_value: z.enum(['null', 'zero']).default('null'),
					zero_division: z.enum(['null', 'error']).default('null'),
					family_id: z.string().nullable().default(null),
					default_of_family: z.boolean().default(false),
					evidence: z.array(z.string()).default([]),
				})
				.strict(),
		),
	})
	.strict();

const normalizedDecimal = (literal: string): string => {
	const match = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(literal);
	if (!match) return literal;
	let digits = `${match[2]}${match[3] ?? ''}`.replace(/^0+/, '');
	if (!digits) return '0';
	let exponent = Number(match[4] ?? 0) - (match[3]?.length ?? 0);
	while (digits.endsWith('0')) {
		digits = digits.slice(0, -1);
		exponent += 1;
	}
	return `${match[1]}${digits}e${exponent}`;
};

/** Inspect the original JSON tokens before JSON.parse can silently round them. */
export const assertExactJsonNumbers = (text: string): void => {
	const tokens = /"(?:\\.|[^"\\])*"|(-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
	for (const token of text.matchAll(tokens)) {
		const literal = token[1];
		if (literal == null) continue;
		const number = Number(literal);
		if (
			!Number.isFinite(number) ||
			(Number.isInteger(number) && !Number.isSafeInteger(number)) ||
			normalizedDecimal(literal) !== normalizedDecimal(String(number))
		) {
			throw new Error(
				`Numeric literal ${literal.slice(0, 60)} cannot be represented exactly in this editor. Quote high precision filter values and expression constants as decimal strings.`,
			);
		}
	}
};

export const predicateScalar = (text: string, dataType: string | undefined): string | boolean =>
	dataType === 'boolean' && ['true', 'false'].includes(text) ? text === 'true' : text;

export const parseModel = (text: string): GovernedModel => {
	assertExactJsonNumbers(text);
	const parsed: unknown = JSON.parse(text);
	// Export envelopes carry provenance as well as the model; provenance never grants trust on import.
	const document =
		parsed && typeof parsed === 'object' && 'model' in parsed ? parsed.model : parsed;
	const result = modelSchema.safeParse(document);
	if (!result.success)
		throw new Error(
			result.error.issues
				.map((issue) => `${issue.path.join('.')}: ${issue.message}`)
				.slice(0, 5)
				.join('\n'),
		);
	return result.data;
};
export const parseExpression = (text: string): Expression | null => {
	assertExactJsonNumbers(text);
	return text.trim() ? expression.parse(JSON.parse(text)) : null;
};
export const splitIds = (value: string): string[] =>
	value === '' ? [] : value.split(',').map((part) => part.trim());
export const newSource = (index: number): SourceBinding => ({
	source_id: `source_${index}`,
	datasource_id: '',
	catalog_table_id: '',
	owner_term_id: '',
	schema_name: 'public',
	table_name: '',
	grain: [],
	fields: [],
	dimensions: [],
	required_filters: [],
	priority: 100,
	pre_aggregated: false,
	structure_fingerprint: '',
});
export const newMetric = (index: number, sourceId = ''): Metric => ({
	metric_id: `metric_${index}`,
	name: `Metric ${index}`,
	description: '',
	owner_term_id: '',
	kind: 'atomic',
	source_ids: sourceId ? [sourceId] : [],
	aggregation: 'count',
	expression: null,
	required_filters: [],
	supported_dimensions: [],
	time_field_id: null,
	time_role: null,
	supported_granularities: [],
	unit: 'number',
	currency_field_id: null,
	fixed_currency: null,
	empty_value: 'null',
	zero_division: 'null',
	family_id: null,
	default_of_family: false,
	evidence: [],
});
export const newModel = (): GovernedModel => ({
	model_id: crypto.randomUUID(),
	name: 'New metric model',
	dialect: 'postgres',
	sources: [newSource(1)],
	relationships: [],
	metrics: [newMetric(1, 'source_1')],
});

export const downloadJson = (name: string, value: unknown) => {
	const url = URL.createObjectURL(
		new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }),
	);
	const link = document.createElement('a');
	link.href = url;
	link.download = name;
	link.click();
	URL.revokeObjectURL(url);
};

/** Shared by the action bar and regression tests: trust transitions act on persisted bytes only. */
export const canTransitionRevision = (
	action: 'validate' | 'certify' | 'activate' | 'deprecate',
	{
		status,
		dirty,
		pendingJson,
		busy,
		canEdit,
		evidence,
	}: {
		status: string;
		dirty: boolean;
		pendingJson: boolean;
		busy: boolean;
		canEdit: boolean;
		evidence: string;
	},
): boolean => {
	if (!canEdit || dirty || pendingJson || busy) return false;
	if (action === 'validate') return status === 'draft' || status === 'validated';
	if (action === 'certify') return status === 'validated' && evidence.trim().length > 0;
	if (action === 'activate') return status === 'certified';
	return status !== 'deprecated';
};

/** Import is a new identity. Exported provenance is deliberately excluded. */
export const prepareDraftImport = (text: string): GovernedModel => ({
	...parseModel(text),
	model_id: crypto.randomUUID(),
});
