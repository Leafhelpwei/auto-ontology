// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import type { useMetricChatModels } from '@/lib/useMetricChatModels';
import { ChatModeControls } from './ChatModeControls';

it('renders model loading failure explicitly, keeps the saved selection, and offers retry', () => {
	// Static fixture for the query state fields this component renders.
	const metrics = {
		activeModels: [],
		loading: false,
		error: new Error('Catalog unavailable'),
		unavailable: 'Metric models could not be loaded: Catalog unavailable',
		staleRevision: false,
		detail: {},
		models: {},
		config: {},
	} as unknown as ReturnType<typeof useMetricChatModels>;
	const html = renderToStaticMarkup(
		<ChatModeControls
			selection={{
				query_mode: 'metrics',
				metric_context: { model_id: 'saved-model', revision: 8, selected_metric_ids: [] },
			}}
			onChange={vi.fn()}
			metrics={metrics}
			disabled={false}
			canReadMetrics
		/>,
	);
	expect(html).toContain('Metric models could not be loaded: Catalog unavailable');
	expect(html).toContain('Retry loading models');
	expect(html).toContain('saved-model · saved selection');
	expect(html).not.toContain('No activated models are available');
	expect(html).toContain('A rejected or ambiguous request stays in this mode');
});
