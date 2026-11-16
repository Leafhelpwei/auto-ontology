// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from '@playwright/test';
export default defineConfig({
	testDir: './e2e',
	timeout: 60_000,
	workers: 1,
	retries: 0,
	reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
	use: {
		baseURL: process.env.APP_URL ?? 'http://127.0.0.1:3000',
		headless: true,
		launchOptions: {
			executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
			args: ['--no-sandbox'],
		},
		screenshot: 'only-on-failure',
		trace: 'off',
	},
});
