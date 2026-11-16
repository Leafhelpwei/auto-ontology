// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Expression, FieldRef, GovernedModel, Predicate } from '@/types/governedMetrics';

/** Allocate within occupied IDs; deleting the middle item cannot collide with the last. */
export const nextStableIndex = (prefix: string, ids: string[]): number => {
	const occupied = new Set(ids);
	let index = 1;
	while (occupied.has(`${prefix}_${index}`)) index += 1;
	return index;
};

/** Drafts may be incomplete; this checks identities/references, not certification semantics. */
export const modelReferenceIssues = (model: GovernedModel): string[] => {
	const issues: string[] = [];
	const sources = new Map(model.sources.map((source) => [source.source_id, source]));
	const relationships = new Set(model.relationships.map((item) => item.relationship_id));
	const metrics = new Set(model.metrics.map((item) => item.metric_id));
	const unique = (ids: string[], owner: string) => {
		const seen = new Set<string>();
		for (const id of ids) {
			if (id && seen.has(id)) issues.push(`${owner}: duplicate ID ${id}`);
			seen.add(id);
		}
	};
	const sourceRef = (id: string, owner: string) => {
		if (id && !sources.has(id)) issues.push(`${owner} references source ${id}`);
	};
	const fieldRef = (field: FieldRef, owner: string) => {
		sourceRef(field.source_id, owner);
		const source = sources.get(field.source_id);
		if (
			source &&
			field.field_id &&
			!source.fields.some((item) => item.field_id === field.field_id)
		)
			issues.push(`${owner} references field ${field.source_id}.${field.field_id}`);
	};
	const predicates = (items: Predicate[], owner: string) =>
		items.forEach((item) => {
			fieldRef(item.field, `${owner} filter`);
			for (const id of item.relationship_path ?? [])
				if (!relationships.has(id))
					issues.push(`${owner} filter references relationship ${id}`);
		});
	unique([...model.sources.map((source) => source.source_id)], 'Sources');
	unique(
		model.relationships.map((item) => item.relationship_id),
		'Relationships',
	);
	unique(
		model.metrics.map((item) => item.metric_id),
		'Metrics',
	);
	for (const source of model.sources) {
		const owner = `Source ${source.source_id}`;
		unique(
			source.fields.map((item) => item.field_id),
			`${owner} fields`,
		);
		unique(
			source.dimensions.map((item) => item.dimension_id),
			`${owner} dimensions`,
		);
		source.grain.forEach((id) =>
			fieldRef({ source_id: source.source_id, field_id: id }, `${owner} grain`),
		);
		predicates(source.required_filters, owner);
		for (const dimension of source.dimensions) {
			fieldRef(dimension.field, `${owner} dimension ${dimension.dimension_id}`);
			for (const id of dimension.relationship_path)
				if (id && !relationships.has(id))
					issues.push(
						`${owner} dimension ${dimension.dimension_id} references relationship ${id}`,
					);
		}
	}
	for (const relationship of model.relationships) {
		const owner = `Relationship ${relationship.relationship_id}`;
		sourceRef(relationship.source_id, owner);
		sourceRef(relationship.target_id, owner);
		for (const key of relationship.keys) {
			fieldRef({ source_id: relationship.source_id, field_id: key.source_field_id }, owner);
			fieldRef({ source_id: relationship.target_id, field_id: key.target_field_id }, owner);
		}
		predicates(relationship.predicates, owner);
	}
	for (const metric of model.metrics) {
		const owner = `Metric ${metric.metric_id}`;
		metric.source_ids.forEach((id) => sourceRef(id, owner));
		predicates(metric.required_filters, owner);
		const dimensions = new Set(
			metric.source_ids.flatMap(
				(id) =>
					sources.get(id)?.dimensions.map((dimension) => dimension.dimension_id) ?? [],
			),
		);
		for (const id of metric.supported_dimensions)
			if (id && !dimensions.has(id)) issues.push(`${owner} references dimension ${id}`);
		const field = (id: string | null) => {
			if (id)
				metric.source_ids.forEach((source_id) =>
					fieldRef({ source_id, field_id: id }, owner),
				);
		};
		field(metric.time_field_id);
		field(metric.currency_field_id);
		const expression = (value: Expression | null) => {
			if (!value) return;
			if (value.kind === 'field') field(value.field_id);
			else if (value.kind === 'metric' && value.metric_id && !metrics.has(value.metric_id))
				issues.push(`${owner} expression references metric ${value.metric_id}`);
			else if (value.kind === 'binary') {
				expression(value.left);
				expression(value.right);
			}
		};
		expression(metric.expression);
	}
	return [...new Set(issues)];
};

export const unsafeModelEdit = (before: GovernedModel, after: GovernedModel): string[] => {
	const previous = new Set(modelReferenceIssues(before));
	return modelReferenceIssues(after).filter((issue) => !previous.has(issue));
};
