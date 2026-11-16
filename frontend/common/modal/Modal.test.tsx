// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment jsdom

import { useState } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Modal } from './Modal';

beforeEach(() => {
	vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([
		{},
	] as unknown as DOMRectList);
});
afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
});
const Example = () => {
	const [open, setOpen] = useState(false);
	const [nested, setNested] = useState(false);
	return (
		<>
			<button onClick={() => setOpen(true)}>Open editor</button>
			<button>Background</button>
			<Modal open={open} onClose={() => setOpen(false)}>
				<h2>Connection editor</h2>
				<input aria-label="Host" />
				<button onClick={() => setNested(true)}>Open confirmation</button>
				<button>Last action</button>
				<Modal open={nested} onClose={() => setNested(false)} ariaLabel="Confirm change">
					<button>Confirm</button>
					<button>Cancel</button>
				</Modal>
			</Modal>
		</>
	);
};
it('names dialogs, traps keyboard focus, isolates the background and restores the trigger', () => {
	const view = render(<Example />);
	const trigger = screen.getByRole('button', { name: 'Open editor' });
	trigger.focus();
	fireEvent.click(trigger);
	expect(screen.getByRole('dialog', { name: 'Connection editor' })).toBeTruthy();
	expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Host' }));
	expect(view.container.inert).toBe(true);
	expect(view.container.getAttribute('aria-hidden')).toBe('true');
	fireEvent.keyDown(document.activeElement!, { key: 'Tab', shiftKey: true });
	expect(document.activeElement?.textContent).toBe('Last action');
	fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
	expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Host' }));
	fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
	expect(screen.queryByRole('dialog')).toBeNull();
	expect(document.activeElement).toBe(trigger);
	expect(view.container.getAttribute('aria-hidden')).toBeNull();
	expect(document.body.style.overflow).toBe('');
});
it('only closes the top nested dialog and keeps the parent scroll lock and focus scope', () => {
	render(<Example />);
	const trigger = screen.getByRole('button', { name: 'Open editor' });
	trigger.focus();
	fireEvent.click(trigger);
	const nestedTrigger = screen.getByRole('button', { name: 'Open confirmation' });
	nestedTrigger.focus();
	fireEvent.click(nestedTrigger);
	expect(screen.getAllByRole('dialog')).toHaveLength(1);
	expect(screen.getByRole('dialog', { name: 'Confirm change' })).toBeTruthy();
	fireEvent.keyDown(document.activeElement!, { key: 'Tab', shiftKey: true });
	expect(document.activeElement?.textContent).toBe('Cancel');
	fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
	expect(screen.getByRole('dialog', { name: 'Connection editor' })).toBeTruthy();
	expect(document.activeElement).toBe(nestedTrigger);
	expect(document.body.style.overflow).toBe('hidden');
	fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
	expect(document.activeElement).toBe(trigger);
});
