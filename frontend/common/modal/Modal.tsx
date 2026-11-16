// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { createContext, useContext, useId, useLayoutEffect, useRef, type ReactNode } from 'react';
import { registerModal } from './modalFocus';
import { createPortal } from 'react-dom';

const ModalDepth = createContext(0);
const layerClasses = ['z-50', 'z-[60]', 'z-[70]', 'z-[80]', 'z-[90]'];

type ModalProps = {
	open: boolean;
	onClose: () => void;
	children: ReactNode;
	className?: string;
	overlayClassName?: string;
	align?: 'center' | 'top';
	ariaLabel?: string;
	ariaLabelledby?: string;
};

export const Modal = ({
	open,
	onClose,
	children,
	className = '',
	overlayClassName,
	align = 'center',
	ariaLabel,
	ariaLabelledby,
}: ModalProps) => {
	const rootRef = useRef<HTMLDivElement>(null);
	const dialogRef = useRef<HTMLDivElement>(null);
	const closeRef = useRef(onClose);
	const titleId = useId();
	const depth = useContext(ModalDepth);
	useLayoutEffect(() => {
		closeRef.current = onClose;
	}, [onClose]);
	useLayoutEffect(() => {
		const root = rootRef.current;
		const dialog = dialogRef.current;
		if (!open || !root || !dialog) return;
		if (!ariaLabel && !ariaLabelledby) {
			const heading = dialog.querySelector<HTMLElement>('h1,h2,h3,h4,[data-dialog-title]');
			if (heading) {
				heading.id ||= titleId;
				dialog.setAttribute('aria-labelledby', heading.id);
				dialog.removeAttribute('aria-label');
			}
		}
		return registerModal({ root, dialog, depth, close: () => closeRef.current() });
	}, [open, depth, titleId, ariaLabel, ariaLabelledby]);

	if (!open) return null;

	return createPortal(
		<ModalDepth.Provider value={depth + 1}>
			<div
				ref={rootRef}
				className={`fixed inset-0 ${layerClasses[Math.min(depth, layerClasses.length - 1)]} flex justify-center ${
					align === 'top' ? 'items-start' : 'items-center'
				} ${overlayClassName ?? (align === 'top' ? 'p-4 pt-16' : 'p-4')}`}
			>
				<div
					className="absolute inset-0 bg-black/40 transition-opacity"
					onClick={onClose}
					aria-hidden
				/>
				<div
					className={`relative rounded-xl border border-zinc-200 bg-white shadow-xl dark:border-zinc-700 dark:bg-zinc-800 ${className}`}
					ref={dialogRef}
					tabIndex={-1}
					aria-label={ariaLabel ?? (ariaLabelledby ? undefined : 'Dialog')}
					aria-labelledby={ariaLabelledby}
					role="dialog"
					aria-modal="true"
				>
					{children}
				</div>
			</div>
		</ModalDepth.Provider>,
		document.body,
	);
};
