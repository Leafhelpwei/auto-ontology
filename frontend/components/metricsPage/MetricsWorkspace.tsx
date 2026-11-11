// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from '@/auth/auth-client';
import { userCan } from '@/auth/permissions';
import { governedMetricsApi } from '@/api/governedMetrics';
import type { ApiError } from '@/api/types';
import { Button } from '@/common/Button';
import { Icon, IconName } from '@/common/icons';
import { ButtonTheme } from '@/enums/button';
import {
	canTransitionRevision,
	downloadJson,
	newModel,
	prepareDraftImport,
} from '@/lib/governedMetrics';
import exampleModel from '@/lib/examples/ordersMetricModel.json';
import type { GovernedModel, ModelDetail, ModelRevision } from '@/types/governedMetrics';
import { Field, inputClass, panelClass, SelectField } from './MetricFields';
import { ModelEditor } from './ModelEditor';
import { MetricQueryPanel } from './MetricQueryPanel';

const messageOf = (error: ApiError) => `${error.code ? `${error.code}: ` : ''}${error.message}`;
const latestRevision = (detail: ModelDetail): ModelRevision | undefined =>
	[...detail.revisions].sort((left, right) => right.revision - left.revision)[0];

const ModelWorkspace = ({
	detail,
	initialModel,
	canEdit,
	enabled,
	onSaved,
	onDirty,
	onClose,
}: {
	detail?: ModelDetail;
	initialModel?: GovernedModel;
	canEdit: boolean;
	enabled: boolean;
	onSaved: (detail: ModelDetail) => void;
	onDirty: (dirty: boolean) => void;
	onClose: () => void;
}) => {
	const first = detail ? latestRevision(detail) : undefined;
	const [revisionNumber, setRevisionNumber] = useState(first?.revision ?? 0);
	const [model, setModel] = useState<GovernedModel>(first?.payload ?? initialModel!);
	const [pendingJson, setPendingJson] = useState(false);
	const [editorEpoch, setEditorEpoch] = useState(0);
	const [tab, setTab] = useState<'model' | 'query'>('model');
	const [busy, setBusy] = useState(false);
	const [evidence, setEvidence] = useState('');
	const [error, setError] = useState<ApiError | null>(null);
	const revision = detail?.revisions.find((item) => item.revision === revisionNumber);
	const dirty = revision ? JSON.stringify(model) !== JSON.stringify(revision.payload) : true;
	const hasUnsaved = dirty || pendingJson;
	// Keep the parent navigation lock in sync even when applying JSON changes
	// both the model and its pending buffer in one batched render.
	useEffect(() => onDirty(hasUnsaved), [hasUnsaved, onDirty]);
	const active = revision?.status === 'certified' && detail?.active_revision === revisionNumber;
	const handleModel = (value: GovernedModel) => {
		setModel(value);
		setError(null);
		onDirty(
			!revision || JSON.stringify(value) !== JSON.stringify(revision.payload) || pendingJson,
		);
	};
	const updatePending = (pending: boolean) => {
		setPendingJson(pending);
		onDirty(dirty || pending);
	};
	const discard = () => {
		if (!revision) {
			onDirty(false);
			onClose();
			return;
		}
		setModel(revision.payload);
		setPendingJson(false);
		setEditorEpoch((value) => value + 1);
		setError(null);
		onDirty(false);
	};
	const save = async () => {
		setBusy(true);
		setError(null);
		try {
			const response = detail
				? await governedMetricsApi.save(model, detail.version)
				: await governedMetricsApi.create(model);
			if (response.error) setError(response as ApiError);
			else {
				onDirty(false);
				onSaved(response.data);
			}
		} finally {
			setBusy(false);
		}
	};
	const transition = async (action: 'validate' | 'certify' | 'activate' | 'deprecate') => {
		if (
			!detail ||
			!revision ||
			!canTransitionRevision(action, {
				status: revision.status,
				dirty,
				pendingJson,
				busy,
				canEdit,
				evidence,
			})
		)
			return;
		setBusy(true);
		setError(null);
		try {
			const response = await governedMetricsApi.transition(
				detail.model_id,
				action,
				revision.revision,
				detail.version,
				evidence,
			);
			if (response.error) setError(response as ApiError);
			else onSaved(response.data);
		} finally {
			setBusy(false);
		}
	};
	const exportRevision = async () => {
		if (!detail || !revision) return;
		setBusy(true);
		setError(null);
		try {
			const response = await governedMetricsApi.export(detail.model_id, revision.revision);
			if (response.error) setError(response as ApiError);
			else downloadJson(`${detail.model_id}-r${revision.revision}.json`, response.data);
		} finally {
			setBusy(false);
		}
	};
	const allowed = (action: 'validate' | 'certify' | 'activate' | 'deprecate') =>
		revision &&
		canTransitionRevision(action, {
			status: revision.status,
			dirty,
			pendingJson,
			busy,
			canEdit,
			evidence,
		});
	return (
		<div className="min-w-0 space-y-5">
			<div className="flex flex-wrap items-start justify-between gap-4">
				<div>
					<h2 className="text-xl font-semibold">{detail?.name ?? 'New metric model'}</h2>
					<p className="mt-1 text-sm text-secondary">
						{detail
							? `Model version ${detail.version} · Active revision ${detail.active_revision ?? 'none'}${detail.archived ? ' · Archived' : ''}`
							: 'Create a draft, bind it to the Catalog, then validate and certify.'}
					</p>
				</div>
				<div className="flex flex-wrap gap-2">
					{detail && (
						<Button
							theme={ButtonTheme.Secondary}
							disabled={busy}
							onClick={() => void exportRevision()}
						>
							Export saved revision
						</Button>
					)}
					{canEdit && (
						<>
							<Button
								theme={ButtonTheme.Secondary}
								disabled={busy || !hasUnsaved}
								onClick={discard}
							>
								Discard edits
							</Button>
							<Button
								disabled={busy || !dirty || pendingJson}
								loading={busy}
								onClick={() => void save()}
							>
								{detail ? 'Save new draft revision' : 'Create draft'}
							</Button>
						</>
					)}
				</div>
			</div>
			{error && (
				<div
					role="alert"
					className="space-y-3 rounded-lg bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950 dark:text-red-200"
				>
					<p>{messageOf(error)}</p>
					{error.status === 409 && (
						<>
							<p>
								This model changed on the server. Your edits are retained. Download
								them before reloading the latest model.
							</p>
							<div className="flex flex-wrap gap-2">
								<Button
									theme={ButtonTheme.Secondary}
									onClick={() =>
										downloadJson(`${model.model_id}-unsaved.json`, model)
									}
								>
									Download local edits
								</Button>
								<Button
									theme={ButtonTheme.Secondary}
									onClick={async () => {
										if (!detail) return;
										const response = await governedMetricsApi.get(
											detail.model_id,
										);
										if (response.error) setError(response as ApiError);
										else {
											onDirty(false);
											onSaved(response.data);
										}
									}}
								>
									Reload latest · discard local edits
								</Button>
							</div>
						</>
					)}
					{error.details != null && (
						<details>
							<summary className="cursor-pointer">Error details</summary>
							<pre className="mt-2 overflow-auto text-xs">
								{JSON.stringify(error.details, null, 2)}
							</pre>
						</details>
					)}
				</div>
			)}
			{detail && revision && (
				<section className={`${panelClass} space-y-4`}>
					<div className="grid items-end gap-4 md:grid-cols-3">
						<SelectField
							label="Saved revision"
							disabled={hasUnsaved || busy}
							value={String(revisionNumber)}
							options={detail.revisions.map((item) => ({
								value: String(item.revision),
								label: `r${item.revision} · ${item.status}${item.revision === detail.active_revision ? ' · active' : ''}`,
							}))}
							onChange={(value) => {
								const next = detail.revisions.find(
									(item) => item.revision === Number(value),
								);
								if (next && !hasUnsaved && !busy) {
									setRevisionNumber(next.revision);
									setModel(next.payload);
									setPendingJson(false);
									setEditorEpoch((value) => value + 1);
									setError(null);
								}
							}}
						/>
						<div className="text-sm">
							<p className="font-medium">
								{hasUnsaved
									? 'Unsaved changes · certification disabled'
									: `Saved revision ${revisionNumber} · ${revision.status}`}
							</p>
							<p className="mt-1 text-xs text-secondary">
								Save creates a new draft. Existing certified revisions retain their
								original content.
							</p>
						</div>
						<p className="break-all font-mono text-xs text-secondary">
							Fingerprint: {revision.fingerprint}
						</p>
					</div>
					{hasUnsaved && (
						<p className="text-xs text-amber-700 dark:text-amber-300">
							Save or discard before switching revisions. Pending Advanced JSON must
							be applied or discarded first.
						</p>
					)}
					{canEdit && (
						<>
							<Field
								label="Certification evidence"
								hint="Describe the reviewed definition and acceptance checks for this saved revision."
							>
								<textarea
									className={inputClass}
									rows={2}
									value={evidence}
									onChange={(event) => setEvidence(event.target.value)}
									disabled={busy || hasUnsaved}
								/>
							</Field>
							<div className="flex flex-wrap gap-2">
								<Button
									theme={ButtonTheme.Secondary}
									disabled={!allowed('validate')}
									onClick={() => void transition('validate')}
								>
									Validate saved revision
								</Button>
								<Button
									theme={ButtonTheme.Secondary}
									disabled={!allowed('certify')}
									onClick={() => void transition('certify')}
								>
									Certify saved revision
								</Button>
								<Button
									theme={ButtonTheme.Primary}
									disabled={!allowed('activate') || active}
									onClick={() => void transition('activate')}
								>
									Activate certified revision
								</Button>
								<Button
									theme={ButtonTheme.DangerSubtle}
									disabled={!allowed('deprecate')}
									onClick={() => void transition('deprecate')}
								>
									Deprecate revision
								</Button>
							</div>
						</>
					)}
					{revision.validation && (
						<div className="text-sm">
							<p
								className={
									revision.validation.valid
										? 'font-medium text-[#4d7a00] dark:text-[#a3d63a]'
										: 'font-medium text-red-600'
								}
							>
								{revision.validation.valid
									? 'Saved revision validation passed'
									: 'Validation issues'}
							</p>
							{revision.validation.issues.length > 0 && (
								<ul className="mt-2 list-disc space-y-1 pl-5">
									{revision.validation.issues.map((issue, index) => (
										<li key={index}>
											<span className="font-mono text-xs">
												{issue.code}
												{issue.path ? ` · ${issue.path}` : ''}
											</span>
											: {issue.message}
										</li>
									))}
								</ul>
							)}
						</div>
					)}
					{revision.certification && (
						<details>
							<summary className="cursor-pointer text-sm">
								Certification record
							</summary>
							<pre className="mt-2 overflow-auto text-xs">
								{JSON.stringify(revision.certification, null, 2)}
							</pre>
						</details>
					)}
				</section>
			)}
			<div className="flex gap-2">
				<Button
					theme={tab === 'model' ? ButtonTheme.Primary : ButtonTheme.Secondary}
					onClick={() => setTab('model')}
				>
					Model definition
				</Button>
				<Button
					theme={tab === 'query' ? ButtonTheme.Primary : ButtonTheme.Secondary}
					disabled={!revision}
					onClick={() => setTab('query')}
				>
					Query and evidence
				</Button>
			</div>
			<div hidden={tab !== 'model'}>
				<ModelEditor
					key={`${revisionNumber}-${editorEpoch}`}
					model={model}
					onChange={handleModel}
					onPendingChange={updatePending}
					disabled={!canEdit || busy || Boolean(detail?.archived)}
				/>
			</div>
			{revision && (
				<div hidden={tab !== 'query'}>
					<MetricQueryPanel
						key={revisionNumber}
						model={revision.payload}
						revision={revisionNumber}
						active={active}
						enabled={enabled}
						canEdit={canEdit}
						hasUnsaved={hasUnsaved}
					/>
				</div>
			)}
		</div>
	);
};

export const MetricsWorkspace = () => {
	const { data: session } = useSession();
	const canEdit = session
		? userCan({ id: session.user.id, role: session.user.role ?? null }, { catalog: ['edit'] })
		: false;
	const queryClient = useQueryClient();
	const fileRef = useRef<HTMLInputElement>(null);
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [creating, setCreating] = useState<GovernedModel | null>(null);
	const [dirty, setDirty] = useState(false);
	const [importing, setImporting] = useState(false);
	const [notice, setNotice] = useState('');
	const [error, setError] = useState('');
	const config = useQuery({
		queryKey: ['governed-metrics', 'config'],
		queryFn: async () => {
			const response = await governedMetricsApi.config();
			if (response.error) throw new Error(response.message);
			return response.data;
		},
	});
	const list = useQuery({
		queryKey: ['governed-metrics', 'models'],
		queryFn: async () => {
			const response = await governedMetricsApi.list();
			if (response.error) throw new Error(response.message);
			return response.data;
		},
	});
	const detail = useQuery({
		queryKey: ['governed-metrics', 'model', selectedId],
		enabled: Boolean(selectedId),
		refetchOnWindowFocus: false,
		queryFn: async () => {
			const response = await governedMetricsApi.get(selectedId!);
			if (response.error) throw new Error(response.message);
			return response.data;
		},
	});
	const saved = (value: ModelDetail) => {
		queryClient.setQueryData(['governed-metrics', 'model', value.model_id], value);
		void queryClient.invalidateQueries({ queryKey: ['governed-metrics', 'models'] });
		setSelectedId(value.model_id);
		setCreating(null);
		setDirty(false);
	};
	const importFile = async (file: File) => {
		setImporting(true);
		setError('');
		setNotice('');
		try {
			if (file.size > 5_000_000) throw new Error('Choose a JSON model smaller than 5 MB.');
			const model = prepareDraftImport(await file.text());
			const response = await governedMetricsApi.import(model);
			if (response.error) throw new Error(response.message);
			saved(response.data);
			setNotice(
				'Imported as a new draft. Exported certification and active status were not applied. Rebind Catalog identifiers and validate before certification.',
			);
		} catch (error) {
			setError(error instanceof Error ? error.message : 'Import failed.');
		} finally {
			setImporting(false);
			if (fileRef.current) fileRef.current.value = '';
		}
	};
	return (
		<div className="h-full overflow-y-auto bg-white dark:bg-zinc-950">
			<header className="flex flex-wrap items-center justify-between gap-4 border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
				<div className="flex items-center gap-3">
					<Icon name={IconName.ChartBar} className="h-5 w-5" />
					<div>
						<h1 className="text-lg font-semibold">Governed metrics</h1>
						<p className="mt-1 text-xs text-secondary">
							Catalog bindings · versioned definitions · certified PostgreSQL queries
						</p>
					</div>
				</div>
				<div className="flex flex-wrap gap-2">
					<Button
						theme={ButtonTheme.Secondary}
						onClick={() => downloadJson('orders-metric-template.json', exampleModel)}
					>
						Download example template
					</Button>
					{canEdit && (
						<>
							<Button
								theme={ButtonTheme.Secondary}
								disabled={dirty || importing}
								loading={importing}
								onClick={() => fileRef.current?.click()}
							>
								Import as draft
							</Button>
							<input
								ref={fileRef}
								aria-label="Import metric model JSON"
								type="file"
								accept="application/json,.json"
								className="hidden"
								onChange={(event) => {
									const file = event.target.files?.[0];
									if (file) void importFile(file);
								}}
							/>
							<Button
								disabled={dirty || importing}
								onClick={() => {
									setCreating(newModel());
									setSelectedId(null);
									setDirty(true);
									setNotice('');
									setError('');
								}}
							>
								New model
							</Button>
						</>
					)}
				</div>
			</header>
			<div className="space-y-4 p-6">
				{config.data?.enabled === false && (
					<div
						role="status"
						className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
					>
						Governed query execution is disabled for this deployment. You can manage and
						review model drafts. An administrator can enable governed metrics in the
						server configuration.
					</div>
				)}
				{notice && (
					<p role="status" className="rounded-lg bg-[#76b900]/10 p-3 text-sm">
						{notice}
					</p>
				)}
				{(error || config.error || list.error) && (
					<p
						role="alert"
						className="rounded-lg bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950 dark:text-red-200"
					>
						{error || config.error?.message || list.error?.message}
					</p>
				)}
				<div className="grid gap-6 xl:grid-cols-[240px_minmax(0,1fr)]">
					<aside className="space-y-3">
						<h2 className="text-sm font-semibold">Models</h2>
						{list.isPending && (
							<p role="status" className="text-sm text-secondary">
								Loading models…
							</p>
						)}
						{list.data?.length === 0 && (
							<p className="text-sm text-secondary">
								No metric models yet. Start with a draft or import a model.
							</p>
						)}
						<ul className="space-y-2">
							{list.data?.map((model) => (
								<li key={model.model_id}>
									<button
										type="button"
										disabled={dirty || importing}
										onClick={() => {
											setSelectedId(model.model_id);
											setCreating(null);
											setNotice('');
											setError('');
										}}
										className={`w-full rounded-xl border p-3 text-left text-sm disabled:cursor-default ${selectedId === model.model_id ? 'border-[#76b900] bg-[#76b900]/10' : 'border-zinc-200 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-900'}`}
									>
										<span className="block truncate font-medium">
											{model.name}
										</span>
										<span className="mt-1 block text-xs text-secondary">
											v{model.version} · Active{' '}
											{model.active_revision == null
												? 'none'
												: `r${model.active_revision}`}
											{model.archived ? ' · Archived' : ''}
										</span>
									</button>
								</li>
							))}
						</ul>
						{dirty && (
							<p className="text-xs text-secondary">
								Save or discard edits before opening another model.
							</p>
						)}
						<p className="pt-3 text-xs text-secondary">
							The example template uses fixture Catalog IDs. Replace them with IDs
							from your ingested Catalog before validation.
						</p>
					</aside>
					<main className="min-w-0">
						{creating ? (
							<ModelWorkspace
								key={creating.model_id}
								initialModel={creating}
								canEdit={canEdit}
								enabled={config.data?.enabled === true}
								onSaved={saved}
								onDirty={setDirty}
								onClose={() => setCreating(null)}
							/>
						) : detail.data ? (
							<ModelWorkspace
								key={`${detail.data.model_id}:${detail.data.version}`}
								detail={detail.data}
								canEdit={canEdit}
								enabled={config.data?.enabled === true}
								onSaved={saved}
								onDirty={setDirty}
								onClose={() => setSelectedId(null)}
							/>
						) : selectedId && detail.isPending ? (
							<p role="status" className="text-sm">
								Loading model…
							</p>
						) : detail.error ? (
							<p role="alert" className="text-sm text-red-600">
								{detail.error.message}
							</p>
						) : (
							<div className={`${panelClass} space-y-3`}>
								<h2 className="text-lg font-semibold">
									Define once, query with evidence
								</h2>
								<p className="max-w-2xl text-sm text-secondary">
									Select a model to review its source bindings, metric variants,
									immutable revisions, and query evidence. Certified execution
									uses the active revision and parameterized SQL.
								</p>
								<p className="text-sm text-secondary">
									Supported scope: PostgreSQL, one fact source per query, safe
									dimensional joins, explicit time ranges, and currency-aware
									metrics.
								</p>
							</div>
						)}
					</main>
				</div>
			</div>
		</div>
	);
};
