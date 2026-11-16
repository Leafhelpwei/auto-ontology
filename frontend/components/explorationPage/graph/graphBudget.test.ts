// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { expect, it } from 'vitest';
import { UndirectedGraph } from 'graphology';
import {
	expansionBudgetIssue,
	GRAPH_LIMITS,
	rememberPosition,
	stablePoint,
	type Point,
} from './graphBudget';
it('bounds twenty cumulative expansions on a 500-node graph and deduplicates reversed edges', () => {
	const graph = new UndirectedGraph();
	for (let i = 0; i < 500; i++) graph.addNode(`base-${i}`);
	for (let run = 0; run < 20; run++) {
		const nodes = Array.from({ length: 20 }, (_, i) => ({ id: `extra-${run}-${i}` }));
		const edges = nodes.flatMap((node) => [
			{ source: `base-${run}`, target: node.id },
			{ source: node.id, target: `base-${run}` },
		]);
		expect(
			expansionBudgetIssue(
				graph.nodes(),
				graph.mapEdges((_id, _attributes, source, target) => ({ source, target })),
				run,
				nodes,
				edges,
			),
		).toBeNull();
		nodes.forEach((node) => graph.addNode(node.id));
		edges.forEach((edge) => {
			if (!graph.hasEdge(edge.source, edge.target)) graph.addEdge(edge.source, edge.target);
		});
	}
	expect(graph.order).toBe(900);
	expect(graph.size).toBe(400);
	expect(expansionBudgetIssue(graph.nodes(), [], 20, [{ id: 'overflow' }], [])).toContain(
		'Collapse',
	);
	expect(graph.order).toBe(900);
	expect(
		expansionBudgetIssue(
			graph.nodes(),
			[],
			1,
			Array.from({ length: 101 }, (_, i) => ({ id: `overflow-${i}` })),
			[],
		),
	).toContain('Graph limit');
});
it('rejects excessive dense connections and uses collision-free edge identities', () => {
	const nodes = Array.from({ length: 100 }, (_, i) => `n${i}`);
	const edges = nodes.flatMap((source, i) =>
		nodes.slice(i + 1).map((target) => ({ source, target })),
	);
	expect(expansionBudgetIssue(nodes, [], 0, [], edges)).toContain(
		`${GRAPH_LIMITS.edges} connections`,
	);
});
it('keeps deterministic positions and bounds remembered layouts with an LRU policy', () => {
	const cache = new Map<string, Point>();
	const point = stablePoint('orders', 500, 300);
	expect(stablePoint('orders', 500, 300)).toEqual(point);
	rememberPosition(cache, 'orders', point);
	rememberPosition(cache, 'invalid', { x: NaN, y: 0 });
	expect(cache.has('invalid')).toBe(false);
	for (let i = 0; i < GRAPH_LIMITS.positions; i++)
		rememberPosition(cache, `n${i}`, { x: i, y: -i });
	expect(cache.size).toBe(GRAPH_LIMITS.positions);
	expect(cache.has('orders')).toBe(false);
	rememberPosition(cache, 'n0', { x: 3, y: 4 });
	rememberPosition(cache, 'new', { x: 1, y: 2 });
	expect(cache.has('n0')).toBe(true);
	expect(cache.has('n1')).toBe(false);
});
