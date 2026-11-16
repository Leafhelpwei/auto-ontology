// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ConnectionModal } from './ConnectionModal';
import { ConnectionType } from '@/enums/connection';
import type { Connection } from '@/types/connection';
import { connectionsApi } from '@/api/connections';
vi.mock('@/api/connections', () => ({
	connectionsApi: { test: vi.fn(), create: vi.fn(), update: vi.fn() },
}));
vi.mock('@/common/modal', () => ({
	ModalWithSteps: ({
		children,
		footerActions,
	}: {
		children: ReactNode;
		footerActions: { label: string; disabled?: boolean; onClick?: () => void }[];
	}) => (
		<div>
			{children}
			{footerActions.map((action) => (
				<button key={action.label} disabled={action.disabled} onClick={action.onClick}>
					{action.label}
				</button>
			))}
		</div>
	),
}));
vi.mock('@/components/connectionsPage/steps/ConnectionConnectStep', () => ({
	ConnectionConnectStep: ({
		values,
		onFieldChange,
		onTestConnection,
		testingConnection,
		testSuccessMessage,
	}: {
		values: Record<string, string>;
		onFieldChange: (key: string, value: string) => void;
		onTestConnection: () => void;
		testingConnection: boolean;
		testSuccessMessage: string | null;
	}) => (
		<div>
			<input
				aria-label="Host"
				value={values.host ?? ''}
				onChange={(e) => onFieldChange('host', e.target.value)}
			/>
			<input
				aria-label="Password"
				value={values.password ?? ''}
				onChange={(e) => onFieldChange('password', e.target.value)}
			/>
			<button disabled={testingConnection} onClick={onTestConnection}>
				Test
			</button>
			{testSuccessMessage}
		</div>
	),
}));
const existing = {
	database_name: 'fixture',
	connection: {
		type: ConnectionType.MYSQL,
		host: 'old.example',
		port: '3306',
		user: 'reader',
		database: 'fixture',
	},
} as Connection;
afterEach(() => {
	cleanup();
	vi.resetAllMocks();
});
it('ignores a successful test after credentials change and requires a new test before Save', async () => {
	let finish!: (value: { success: true; schemas: string[] }) => void;
	vi.mocked(connectionsApi.test).mockImplementationOnce(
		() =>
			new Promise((resolve) => {
				finish = resolve;
			}),
	);
	render(<ConnectionModal open connection={existing} onConfirm={vi.fn()} onCancel={vi.fn()} />);
	fireEvent.click(screen.getByText('Test'));
	fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'changed.example' } });
	await act(async () => {
		finish({ success: true, schemas: [] });
	});
	expect(screen.queryByText('Connection successful.')).toBeNull();
	expect((screen.getByText('Save') as HTMLButtonElement).disabled).toBe(true);
	vi.mocked(connectionsApi.test).mockResolvedValueOnce({ success: true, schemas: [] });
	await act(async () => {
		fireEvent.click(screen.getByText('Test'));
	});
	expect(screen.getByText('Connection successful.')).toBeTruthy();
	expect((screen.getByText('Save') as HTMLButtonElement).disabled).toBe(false);
	expect(vi.mocked(connectionsApi.test).mock.calls.at(-1)?.[0]).toMatchObject({
		host: 'changed.example',
	});
});
it('ignores a result arriving after the form is closed and reopened', async () => {
	let finish!: (value: { success: true; schemas: string[] }) => void;
	vi.mocked(connectionsApi.test).mockImplementationOnce(
		() =>
			new Promise((resolve) => {
				finish = resolve;
			}),
	);
	const props = { connection: existing, onConfirm: vi.fn(), onCancel: vi.fn() };
	const view = render(<ConnectionModal {...props} open />);
	fireEvent.click(screen.getByText('Test'));
	view.rerender(<ConnectionModal {...props} open={false} />);
	view.rerender(<ConnectionModal {...props} open />);
	await act(async () => {
		finish({ success: true, schemas: [] });
	});
	expect(screen.queryByText('Connection successful.')).toBeNull();
	expect((screen.getByText('Save') as HTMLButtonElement).disabled).toBe(true);
});
it('sends leading and trailing password whitespace without trimming', async () => {
	vi.mocked(connectionsApi.test).mockResolvedValueOnce({ success: true, schemas: [] });
	render(<ConnectionModal open connection={existing} onConfirm={vi.fn()} onCancel={vi.fn()} />);
	fireEvent.change(screen.getByLabelText('Password'), { target: { value: ' 密码😀 ' } });
	await act(async () => {
		fireEvent.click(screen.getByText('Test'));
	});
	expect(vi.mocked(connectionsApi.test).mock.calls[0][0]).toMatchObject({ password: ' 密码😀 ' });
});
