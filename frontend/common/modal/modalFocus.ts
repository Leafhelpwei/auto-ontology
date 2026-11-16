// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** One stack owns focus, inert backgrounds and scroll locking for nested dialogs. */
type Entry = {
	root: HTMLElement;
	dialog: HTMLElement;
	depth: number;
	close: () => void;
	previous: HTMLElement | null;
};
const stack: Entry[] = [];
const background = new Map<HTMLElement, { inert: boolean; hidden: string | null }>();
let oldOverflow = '';
let observer: MutationObserver | null = null;
const top = () => stack.at(-1);
const focusable = (dialog: HTMLElement) =>
	[
		...dialog.querySelectorAll<HTMLElement>(
			'button, a[href], input, select, textarea, [tabindex]',
		),
	].filter(
		(element) =>
			element.tabIndex >= 0 &&
			!element.matches(':disabled') &&
			!element.closest('[hidden], [inert], [aria-hidden="true"]') &&
			element.getClientRects().length > 0,
	);
const focus = (entry: Entry) => {
	const elements = focusable(entry.dialog);
	const preferred = elements.find((element) =>
		element.matches('[autofocus], [data-autofocus], input, select, textarea'),
	);
	(preferred ?? elements[0] ?? entry.dialog).focus({ preventScroll: true });
};
const isolate = () => {
	const active = top();
	for (const element of document.body.children) {
		if (!(element instanceof HTMLElement)) continue;
		if (!background.has(element))
			background.set(element, {
				inert: element.inert,
				hidden: element.getAttribute('aria-hidden'),
			});
		const original = background.get(element)!;
		const blocked = Boolean(active && element !== active.root);
		element.inert = blocked || original.inert;
		if (blocked) element.setAttribute('aria-hidden', 'true');
		else if (original.hidden == null) element.removeAttribute('aria-hidden');
		else element.setAttribute('aria-hidden', original.hidden);
	}
};
const onKey = (event: KeyboardEvent) => {
	const active = top();
	if (!active) return;
	if (event.key === 'Escape') {
		event.preventDefault();
		event.stopPropagation();
		active.close();
		return;
	}
	if (event.key !== 'Tab') return;
	const elements = focusable(active.dialog);
	const index = elements.indexOf(document.activeElement as HTMLElement);
	if (!elements.length) {
		event.preventDefault();
		active.dialog.focus();
	} else if (event.shiftKey && index <= 0) {
		event.preventDefault();
		elements.at(-1)!.focus();
	} else if (!event.shiftKey && (index < 0 || index === elements.length - 1)) {
		event.preventDefault();
		elements[0].focus();
	}
};
const onFocus = (event: FocusEvent) => {
	const active = top();
	if (active && !active.dialog.contains(event.target as Node)) focus(active);
};

export const registerModal = (entry: Omit<Entry, 'previous'>): (() => void) => {
	const current: Entry = {
		...entry,
		previous: document.activeElement instanceof HTMLElement ? document.activeElement : null,
	};
	if (!stack.length) {
		oldOverflow = document.body.style.overflow;
		document.body.style.overflow = 'hidden';
		document.addEventListener('keydown', onKey, true);
		document.addEventListener('focusin', onFocus, true);
		observer = new MutationObserver(isolate);
		observer.observe(document.body, { childList: true });
	}
	stack.push(current);
	stack.sort((a, b) => a.depth - b.depth);
	isolate();
	if (top() === current) focus(current);
	return () => {
		const wasTop = top() === current;
		stack.splice(stack.indexOf(current), 1);
		isolate();
		if (!stack.length) {
			document.body.style.overflow = oldOverflow;
			document.removeEventListener('keydown', onKey, true);
			document.removeEventListener('focusin', onFocus, true);
			observer?.disconnect();
			observer = null;
			for (const [element, original] of background) {
				element.inert = original.inert;
				if (original.hidden == null) element.removeAttribute('aria-hidden');
				else element.setAttribute('aria-hidden', original.hidden);
			}
			background.clear();
		}
		if (wasTop) {
			const active = top();
			if (
				current.previous?.isConnected &&
				(!active || active.dialog.contains(current.previous))
			)
				current.previous.focus({ preventScroll: true });
			else if (active) focus(active);
		}
	};
};
