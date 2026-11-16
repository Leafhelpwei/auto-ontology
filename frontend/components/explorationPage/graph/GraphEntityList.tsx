// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from 'react';
import { Button } from '@/common/Button';
import { ButtonTheme, Size } from '@/enums/button';
import { usePagination } from '@/hooks/usePagination';
import type { GraphSnapshot } from './graphBudget';

/** The DOM path shares the same selection and expansion handlers as the canvas. */
export const GraphEntityList = ({
	snapshot,
	activeId,
	onSelect,
	onExpand,
	onConnection,
}: {
	snapshot: GraphSnapshot;
	activeId: string | null;
	onSelect: (id: string) => void;
	onExpand: (id: string) => void;
	onConnection: (id: string) => void;
}) => {
	const [search, setSearch] = useState('');
	const [tab, setTab] = useState<'nodes' | 'edges'>('nodes');
	const byId = new Map(snapshot.nodes.map((node) => [node.id, node]));
	const matches = (text: string) => text.toLocaleLowerCase().includes(search.toLocaleLowerCase());
	const nodes = snapshot.nodes.filter((node) => matches(`${node.name} ${node.kind} ${node.id}`));
	const edges = snapshot.edges.filter((edge) =>
		matches(`${byId.get(edge.source)?.name} ${byId.get(edge.target)?.name} ${edge.kind}`),
	);
	const total = tab === 'nodes' ? nodes.length : edges.length;
	const { skip, page, pageSize, setPage } = usePagination({
		totalItems: total,
		resetKey: `${tab}:${search}`,
	});
	return (
		<section
			aria-label="Graph entities and connections"
			className="absolute bottom-4 left-4 top-20 z-20 flex w-80 max-w-[calc(100%-2rem)] flex-col gap-3 overflow-y-auto rounded-xl border border-zinc-300 bg-white p-4 shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
		>
			<label className="text-sm">
				Search loaded graph
				<input
					className="mt-1 w-full rounded border p-2"
					value={search}
					onChange={(event) => setSearch(event.target.value)}
				/>
			</label>
			<div className="flex gap-2">
				{(['nodes', 'edges'] as const).map((value) => (
					<Button
						key={value}
						size={Size.SMALL}
						theme={ButtonTheme.Secondary}
						aria-pressed={tab === value}
						onClick={() => setTab(value)}
					>
						{value === 'nodes' ? 'Entities' : 'Connections'}
					</Button>
				))}
			</div>
			<p role="status" className="text-xs text-secondary">
				{total} matches · {snapshot.nodes.length} loaded entities · {snapshot.edges.length}{' '}
				connections
			</p>
			<ul className="space-y-3">
				{tab === 'nodes'
					? nodes.slice(skip, skip + pageSize).map((node) => (
							<li
								key={node.id}
								className="space-y-2 rounded border border-zinc-200 p-2 dark:border-zinc-700"
							>
								<Button
									theme={ButtonTheme.Minimal}
									aria-pressed={activeId === node.id}
									onClick={() => onSelect(node.id)}
								>
									{node.name} · {node.kind}
								</Button>
								<Button
									size={Size.SMALL}
									theme={ButtonTheme.Secondary}
									disabled={node.kind === 'customAnalysis'}
									aria-label={`${node.expanded ? 'Collapse' : 'Expand'} ${node.name}`}
									onClick={() => onExpand(node.id)}
								>
									{node.expanded ? 'Collapse' : 'Expand'} connections
								</Button>
							</li>
						))
					: edges.slice(skip, skip + pageSize).map((edge) => (
							<li
								key={edge.id}
								className="space-y-2 rounded border border-zinc-200 p-2 dark:border-zinc-700"
							>
								<p className="text-xs">{edge.kind} · undirected connection</p>
								<Button
									theme={ButtonTheme.Minimal}
									onClick={() => onSelect(edge.source)}
								>
									{byId.get(edge.source)?.name ?? edge.source}
								</Button>
								<span className="block text-xs">connected to</span>
								<Button
									theme={ButtonTheme.Minimal}
									onClick={() => onSelect(edge.target)}
								>
									{byId.get(edge.target)?.name ?? edge.target}
								</Button>
								{edge.kind === 'relationship' && (
									<Button
										size={Size.SMALL}
										theme={ButtonTheme.Secondary}
										onClick={() => onConnection(edge.id)}
									>
										View connection path
									</Button>
								)}
							</li>
						))}
			</ul>
			<div className="flex items-center justify-between gap-2">
				<Button
					size={Size.SMALL}
					theme={ButtonTheme.Secondary}
					disabled={page === 1}
					onClick={() => setPage(page - 1)}
				>
					Previous entities
				</Button>
				<span className="text-xs">
					{page}/{Math.max(1, Math.ceil(total / pageSize))}
				</span>
				<Button
					size={Size.SMALL}
					theme={ButtonTheme.Secondary}
					disabled={skip + pageSize >= total}
					onClick={() => setPage(page + 1)}
				>
					Next entities
				</Button>
			</div>
		</section>
	);
};
