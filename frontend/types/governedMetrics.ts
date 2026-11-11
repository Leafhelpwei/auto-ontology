// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type Scalar = string | number | boolean | null;
export type FieldRef = { source_id: string; field_id: string };
export type Predicate = {
	field: FieldRef;
	operator:
		| 'eq'
		| 'ne'
		| 'gt'
		| 'gte'
		| 'lt'
		| 'lte'
		| 'in'
		| 'not_in'
		| 'is_null'
		| 'is_not_null';
	value: Scalar | Scalar[];
};
export type FieldBinding = {
	field_id: string;
	catalog_column_id: string;
	column_name: string;
	data_type: 'number' | 'string' | 'boolean' | 'date' | 'datetime';
	nullable: boolean;
};
export type DimensionBinding = {
	dimension_id: string;
	field: FieldRef;
	relationship_path: string[];
};
export type SourceBinding = {
	source_id: string;
	datasource_id: string;
	catalog_table_id: string;
	owner_term_id: string;
	schema_name: string;
	table_name: string;
	grain: string[];
	fields: FieldBinding[];
	dimensions: DimensionBinding[];
	required_filters: Predicate[];
	priority: number;
	pre_aggregated: boolean;
	structure_fingerprint: string;
};
export type Relationship = {
	relationship_id: string;
	source_id: string;
	target_id: string;
	role: string;
	keys: { source_field_id: string; target_field_id: string }[];
	cardinality: 'many_to_one' | 'one_to_one' | 'one_to_many' | 'many_to_many';
	join_type: 'inner' | 'left';
	predicates: Predicate[];
};
export type Expression =
	| { kind: 'field'; field_id: string }
	| { kind: 'number'; value: string | number }
	| { kind: 'metric'; metric_id: string }
	| {
			kind: 'binary';
			operator: 'add' | 'subtract' | 'multiply' | 'divide';
			left: Expression;
			right: Expression;
	  };
export type Granularity = 'day' | 'month' | 'year';
export type Metric = {
	metric_id: string;
	name: string;
	description: string;
	owner_term_id: string;
	kind: 'atomic' | 'composite';
	source_ids: string[];
	aggregation: 'sum' | 'count' | 'count_distinct' | null;
	expression: Expression | null;
	required_filters: Predicate[];
	supported_dimensions: string[];
	time_field_id: string | null;
	time_role: string | null;
	supported_granularities: Granularity[];
	unit: string;
	currency_field_id: string | null;
	fixed_currency: string | null;
	empty_value: 'null' | 'zero';
	zero_division: 'null' | 'error';
	family_id: string | null;
	default_of_family: boolean;
	evidence: string[];
};
export type GovernedModel = {
	model_id: string;
	name: string;
	dialect: 'postgres';
	sources: SourceBinding[];
	relationships: Relationship[];
	metrics: Metric[];
};
export type MetricQueryRequest = {
	metric_ids: string[];
	dimensions: string[];
	filters: Predicate[];
	time: {
		start: string;
		end_exclusive: string;
		timezone: string;
		time_role: string;
		granularity: Granularity | null;
	} | null;
	all_time: boolean;
	order_by: { field_id: string; direction: 'asc' | 'desc' }[];
	limit: number;
};
export type ValidationIssue = { code: string; message: string; path: string };
export type ValidationReport = { valid: boolean; issues: ValidationIssue[] };
export type MetricPlan = {
	sql: string;
	params: Record<string, Scalar>;
	datasource_id: string;
	source_ids: string[];
	metric_fingerprints: Record<string, string>;
	model_fingerprint: string;
	request_fingerprint: string;
	evidence: Record<string, unknown>;
};
export type ModelRevision = {
	revision: number;
	status: 'draft' | 'validated' | 'certified' | 'deprecated';
	payload: GovernedModel;
	fingerprint: string;
	validation: ValidationReport | null;
	certification: Record<string, unknown> | null;
	created_by: string | null;
	created_at: string;
};
export type ModelSummary = {
	model_id: string;
	name: string;
	owner_id: string | null;
	version: number;
	latest_revision: number;
	active_revision: number | null;
	archived: boolean;
};
export type ModelDetail = ModelSummary & { revisions: ModelRevision[] };
export type MetricQueryResult = {
	plan: MetricPlan;
	trusted: boolean;
	revision: number;
	columns?: string[];
	column_metadata?: {
		name: string;
		postgres_type_oid: number;
		json_encoding: 'decimal-string' | 'integer-string' | 'native';
	}[];
	rows?: unknown[][] | Record<string, unknown>[];
	row_count?: number;
	truncated?: boolean;
};

export type InterpretationResult = {
	status: 'ready' | 'clarification' | 'rejected';
	query?: MetricQueryRequest | null;
	plan?: MetricPlan | null;
	message: string;
	code: string;
	options: { metric_id: string; name: string; description: string; family_id: string | null }[];
};
