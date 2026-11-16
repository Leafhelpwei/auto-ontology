// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import type { UndirectedGraph } from 'graphology';
import { GraphCanvas, type GraphController } from './GraphCanvas';
import { ExplorationLayer } from '@/enums/exploration';
import { TableType } from '@/enums/datasources';
import type { ExplorationGraph } from '@/types/exploration';
import type { GraphSnapshot, Point } from './graphBudget';
const renderer = vi.hoisted(() => ({ graph: null as UndirectedGraph | null, kill: vi.fn() }));
vi.mock('sigma', () => ({
	default: class {
		constructor(graph: UndirectedGraph) {
			renderer.graph = graph;
		}
		refresh() {}
		on() {}
		resize() {}
		setSetting() {}
		setCustomBBox() {}
		kill = renderer.kill;
		graphToViewport() {
			return { x: 500, y: 300 };
		}
		getDimensions() {
			return { width: 1000, height: 600 };
		}
		getBBox() {
			return { x: [-500, 500], y: [-300, 300] };
		}
		getCustomBBox() {
			return this.getBBox();
		}
		getCamera() {
			return {
				on() {},
				enabledPanning: true,
				animatedReset: () => Promise.resolve(),
				animatedZoom() {},
			};
		}
		getMouseCaptor() {
			return { on() {}, isMouseDown: false };
		}
	},
}));
vi.mock('sigma/rendering', () => ({
	createNodeCompoundProgram: () => class {},
	NodeCircleProgram: class {},
}));
vi.mock('@sigma/node-border', () => ({ createNodeBorderProgram: () => class {} }));
beforeEach(() => {
	vi.stubGlobal('matchMedia', () => ({
		matches: false,
		addEventListener() {},
		removeEventListener() {},
	}));
	vi.stubGlobal(
		'ResizeObserver',
		class {
			observe() {}
			disconnect() {}
		},
	);
});
afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});
it('runs real graph/D3 expansion and collapse under budgets, preserving cached positions', async () => {
	const graph: ExplorationGraph = {
		nodes: Array.from({ length: 500 }, (_, i) => ({
			id: `t${i}`,
			name: `Table ${i}`,
			description: null,
			layer: ExplorationLayer.Data,
			nodeType: TableType.BASE_TABLE,
			relationshipCount: 0,
			databaseId: 'db',
			databaseName: 'Sales',
			schemaId: 'schema',
			schemaName: 'public',
			columnsCount: 0,
			sqlCount: 0,
			termsCount: 0,
			zones: [],
		})),
		links: [],
	};
	let controller!: GraphController;
	let snapshot!: GraphSnapshot;
	const positions = new Map<string, Point>();
	const props = {
		graph,
		positions,
		activeNodeId: null,
		highlightedPath: null,
		onSelectNode: vi.fn(),
		onSelectEdge: vi.fn(),
		onClickNode: vi.fn(),
		onDoubleClickNode: vi.fn(),
		onHoverNode: vi.fn(),
		onControllerChange: (value: GraphController | null) => {
			if (value) controller = value;
		},
		onGraphChange: (value: GraphSnapshot) => {
			snapshot = value;
		},
	};
	const view = render(<GraphCanvas {...props} />);
	act(() => controller.setLayoutPaused(true));
	const started = performance.now();
	for (let run = 0; run < 20; run++) {
		const nodes = Array.from({ length: 20 }, (_, i) => ({
			id: `c${run}-${i}`,
			label: `Column ${run}-${i}`,
			kind: 'column' as const,
		}));
		const edges = nodes.map((node) => ({ source: `t${run}`, target: node.id }));
		expect(controller.addExpansion(`t${run}`, nodes, edges)).toBe(true);
	}
	expect(snapshot.nodes).toHaveLength(900);
	expect(snapshot.expansions).toBe(20);
	expect(controller.addExpansion('t20', [], [])).toBe(false);
	expect(snapshot.notice).toContain('Collapse');
	const point = {
		x: renderer.graph!.getNodeAttribute('c0-0', 'x'),
		y: renderer.graph!.getNodeAttribute('c0-0', 'y'),
	};
	controller.removeExpansion('t0');
	expect(snapshot.nodes).toHaveLength(880);
	expect(
		controller.addExpansion(
			't0',
			[{ id: 'c0-0', label: 'Restored column', kind: 'column' }],
			[{ source: 't0', target: 'c0-0' }],
		),
	).toBe(true);
	expect(renderer.graph!.getNodeAttribute('c0-0', 'x')).toBe(point.x);
	expect(renderer.graph!.getNodeAttribute('c0-0', 'y')).toBe(point.y);
	expect(controller.addExpansion('t0', [], [])).toBe(false);
	expect(performance.now() - started).toBeLessThan(2000);
	controller.setLayoutPaused(false);
	controller.setLayoutPaused(true);
	const before = renderer.graph!.getNodeAttribute('c0-0', 'x');
	await act(async () => {
		await new Promise((resolve) => setTimeout(resolve, 30));
	});
	expect(renderer.graph!.getNodeAttribute('c0-0', 'x')).toBe(before);
	view.unmount();
	expect(renderer.kill).toHaveBeenCalledOnce();
	expect(positions.has('t0')).toBe(true);
});
