// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export const GRAPH_LIMITS = { nodes: 1000, edges: 3000, expansions: 20, positions: 2000 } as const;
export type Point = { x: number; y: number };
export const edgeIdentity = (source: string, target: string): string =>
	JSON.stringify([source, target].sort());
export const expansionBudgetIssue = (
	currentNodes: string[],
	currentEdges: { source: string; target: string }[],
	expansionCount: number,
	nodes: { id: string }[],
	edges: { source: string; target: string }[],
): string | null => {
	const ids = new Set([...currentNodes, ...nodes.map((node) => node.id)]);
	const links = new Set(currentEdges.map((edge) => edgeIdentity(edge.source, edge.target)));
	for (const edge of edges)
		if (edge.source !== edge.target && ids.has(edge.source) && ids.has(edge.target))
			links.add(edgeIdentity(edge.source, edge.target));
	if (
		ids.size > GRAPH_LIMITS.nodes ||
		links.size > GRAPH_LIMITS.edges ||
		expansionCount >= GRAPH_LIMITS.expansions
	)
		return `Graph limit reached (${GRAPH_LIMITS.nodes} nodes, ${GRAPH_LIMITS.edges} connections, ${GRAPH_LIMITS.expansions} expanded objects). Collapse an expansion or narrow the database filter before expanding more.`;
	return null;
};
/** Stable scatter even before a node has a remembered layout position. */
export const stablePoint = (id: string, width: number, height: number): Point => {
	let hash = 2166136261;
	for (let i = 0; i < id.length; i++) hash = Math.imul(hash ^ id.charCodeAt(i), 16777619);
	const x = (hash >>> 0) / 0x100000000;
	hash = Math.imul(hash ^ (hash >>> 16), 2246822507);
	return { x: (x - 0.5) * width, y: ((hash >>> 0) / 0x100000000 - 0.5) * height };
};
export const rememberPosition = (cache: Map<string, Point>, id: string, point: Point): void => {
	if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return;
	cache.delete(id);
	cache.set(id, point);
	while (cache.size > GRAPH_LIMITS.positions) cache.delete(cache.keys().next().value!);
};
export type GraphSnapshot = {
	nodes: { id: string; name: string; kind: string; expanded: boolean }[];
	edges: { id: string; source: string; target: string; kind: string }[];
	expansions: number;
	notice: string;
};
