// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom

import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ChatInput } from './ChatInput';

vi.mock('@/common/icons', () => ({
	Icon: () => <span aria-hidden="true" />,
	IconName: { Send: 'send', Stop: 'stop' },
}));

afterEach(cleanup);
const input = () => screen.getByRole('textbox') as HTMLTextAreaElement;
const type = (value: string) => fireEvent.input(input(), { target: { value } });
const enter = (extra = {}) => fireEvent.keyDown(input(), { key: 'Enter', ...extra });

it('keeps IME confirmation local and submits only the next independent Enter', async () => {
	const send = vi.fn().mockResolvedValue(true);
	render(<ChatInput onSend={send} onStop={vi.fn()} isLoading={false} />);
	fireEvent.compositionStart(input());
	type('中文查询');
	enter();
	fireEvent.compositionEnd(input());
	enter({ keyCode: 229 });
	enter({ isComposing: true });
	enter({ shiftKey: true });
	expect(send).not.toHaveBeenCalled();
	await act(async () => {
		enter();
	});
	expect(send).toHaveBeenCalledExactlyOnceWith('中文查询');
});

it('restores an ordinary refusal, prevents duplicate sends and preserves spaces', async () => {
	let resolve!: (accepted: boolean) => void;
	const send = vi.fn(
		() =>
			new Promise<boolean>((done) => {
				resolve = done;
			}),
	);
	render(<ChatInput onSend={send} onStop={vi.fn()} isLoading={false} />);
	type('  当前问题  ');
	enter();
	type('another');
	enter();
	expect(send).toHaveBeenCalledTimes(1);
	// A newer edit, even if subsequently emptied, must not be replaced by the old draft.
	type('');
	await act(async () => {
		resolve(false);
	});
	expect(input().value).toBe('');
	type('  当前问题  ');
	enter();
	await act(async () => {
		resolve(false);
	});
	expect(input().value).toBe('  当前问题  ');
});

it('never restores a late refusal into another composer or clears its draft', async () => {
	let resolve!: (accepted: boolean) => void;
	const send = vi.fn(
		() =>
			new Promise<boolean>((done) => {
				resolve = done;
			}),
	);
	const view = render(<ChatInput key="A" onSend={send} onStop={vi.fn()} isLoading={false} />);
	type('A 的问题');
	enter();
	view.rerender(<ChatInput key="B" onSend={send} onStop={vi.fn()} isLoading={false} />);
	type('B 的草稿');
	await act(async () => {
		resolve(false);
	});
	expect(input().value).toBe('B 的草稿');
});

it('keeps text after an unexpected rejected send promise', async () => {
	render(
		<ChatInput
			onSend={vi.fn().mockRejectedValue(new Error('offline'))}
			onStop={vi.fn()}
			isLoading={false}
		/>,
	);
	type('keep me');
	await act(async () => {
		enter();
	});
	expect(input().value).toBe('keep me');
});
