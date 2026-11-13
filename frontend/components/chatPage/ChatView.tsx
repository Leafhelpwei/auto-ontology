// SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
// All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Spinner } from '@nvidia/foundations-react-core';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useChat } from '@/lib/useChat';
import { useMetricChatModels } from '@/lib/useMetricChatModels';
import {
	exploreSelection,
	selectionFromContext,
	selectionFromMessages,
} from '@/lib/chatMetricContext';
import { useSession } from '@/auth/auth-client';
import { userCan } from '@/auth/permissions';
import type {
	ChatSelection,
	ChatQueryContext,
	Conversation,
	MetricsQueryContext,
} from '@/types/chat';
import { conversationsApi, toConversation, type ConversationSummary } from '@/api/conversations';
import { semanticCompilationApi } from '@/api/settings';
import { ChatSidebar } from './ChatSidebar';
import { MessageList } from './MessageList';
import { ChatInput } from './ChatInput';
import { SemanticNotReady } from './SemanticNotReady';
import { ChatModeControls } from './ChatModeControls';

export const ChatView = () => {
	const router = useRouter();
	const pathname = usePathname();
	const searchParams = useSearchParams();
	const rawFocus = searchParams.get('focus');
	const focusId = rawFocus != null && rawFocus.trim() !== '' ? rawFocus.trim() : null;
	const { data: session } = useSession();
	const canReadMetrics = session
		? userCan({ id: session.user.id, role: session.user.role ?? null }, { catalog: ['read'] })
		: false;
	const [selection, setSelection] = useState<ChatSelection>(exploreSelection);
	const metricModels = useMetricChatModels(selection, canReadMetrics);
	const [activeConvId, setActiveConvId] = useState<string | null>(focusId);
	const [observedFocus, setObservedFocus] = useState(focusId);
	const [sidebarOpen, setSidebarOpen] = useState(false);
	const [conversations, setConversations] = useState<Conversation[]>([]);
	const [sidebarLoading, setSidebarLoading] = useState(true);
	const [messageListLoading, setMessageListLoading] = useState<boolean>(focusId != null);
	const [semanticReady, setSemanticReady] = useState<boolean | null>(null);
	const [historyError, setHistoryError] = useState<string | null>(null);
	const [historyRetry, setHistoryRetry] = useState(0);
	const [notice, setNotice] = useState<string | null>(null);
	const loadedFocusRef = useRef<string | null>(null);
	const viewEpochRef = useRef(0);
	const [submitting, setSubmitting] = useState(false);
	const {
		messages,
		setMessages,
		steps,
		liveSql,
		isLoading,
		requestError,
		sendMessage,
		resumeIfRunning,
		stopGeneration,
		clearConversation,
	} = useChat(
		useCallback((context: ChatQueryContext) => setSelection(selectionFromContext(context)), []),
	);

	// Reset route-owned controls before loading another thread. A locally
	// created conversation already matches activeConvId, preserving its first send.
	if (observedFocus !== focusId) {
		setObservedFocus(focusId);
		if (activeConvId !== focusId) {
			setActiveConvId(focusId);
			setSelection(exploreSelection());
			setNotice(null);
			setHistoryError(null);
			setMessageListLoading(Boolean(focusId));
		}
	}

	const updateFocusInUrl = useCallback(
		(id: string | null) => {
			const params = new URLSearchParams(searchParams.toString());
			if (id) params.set('focus', id);
			else params.delete('focus');
			const query = params.toString();
			router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
		},
		[router, pathname, searchParams],
	);

	const refreshConversations = useCallback(async () => {
		const summaries = await conversationsApi.list();
		setConversations(
			summaries.map((summary: ConversationSummary) => ({
				id: summary.id,
				title: summary.title || 'New conversation',
				messages: [],
				createdAt: new Date(summary.created_at).getTime(),
			})),
		);
	}, []);

	useEffect(() => {
		void refreshConversations()
			.catch(() => setHistoryError('Conversation history could not be loaded.'))
			.finally(() => setSidebarLoading(false));
		semanticCompilationApi
			.getStatus()
			.then((response) => setSemanticReady(response.calculated))
			.catch(() => setSemanticReady(false));
	}, [refreshConversations]);

	useEffect(() => {
		if (!focusId) {
			if (loadedFocusRef.current != null) {
				clearConversation();
			}
			loadedFocusRef.current = null;
			return;
		}
		if (loadedFocusRef.current === focusId) return;
		loadedFocusRef.current = focusId;
		viewEpochRef.current += 1;
		clearConversation();
		let active = true;
		conversationsApi
			.get(focusId)
			.then((detail) => {
				if (!active) return;
				const conversation = toConversation(detail);
				setMessages(conversation.messages);
				setSelection(selectionFromMessages(conversation.messages));
				resumeIfRunning(detail.id);
			})
			.catch(() => {
				if (!active) return;
				setHistoryError('This conversation could not be loaded. Select it again to retry.');
				loadedFocusRef.current = null;
			})
			.finally(() => {
				if (active) setMessageListLoading(false);
			});
		return () => {
			active = false;
		};
	}, [focusId, historyRetry, setMessages, resumeIfRunning, clearConversation]);

	const handleNewChat = useCallback(async () => {
		viewEpochRef.current += 1;
		setSubmitting(false);
		clearConversation();
		setSelection(exploreSelection());
		setActiveConvId(null);
		setSidebarOpen(false);
		setHistoryError(null);
		setNotice(null);
		loadedFocusRef.current = null;
		updateFocusInUrl(null);
		await refreshConversations().catch(() =>
			setHistoryError('Conversation history could not be loaded.'),
		);
	}, [clearConversation, refreshConversations, updateFocusInUrl]);

	const handleSelectConversation = useCallback(
		(id: string) => {
			setSidebarOpen(false);
			if (id === focusId && loadedFocusRef.current === id) return;
			viewEpochRef.current += 1;
			setSubmitting(false);
			if (id === focusId) {
				setMessageListLoading(true);
				setHistoryError(null);
				setHistoryRetry((value) => value + 1);
			} else updateFocusInUrl(id);
		},
		[focusId, updateFocusInUrl],
	);

	const handleRename = useCallback(
		async (id: string, title: string) => {
			try {
				await conversationsApi.rename(id, title);
				await refreshConversations();
			} catch {
				setHistoryError('The conversation could not be renamed.');
			}
		},
		[refreshConversations],
	);
	const handleDelete = useCallback(
		async (id: string) => {
			try {
				await conversationsApi.delete(id);
				if (activeConvId === id) await handleNewChat();
				else await refreshConversations();
			} catch {
				setHistoryError('The conversation could not be deleted.');
			}
		},
		[activeConvId, handleNewChat, refreshConversations],
	);

	const handleSend = useCallback(
		async (text: string): Promise<boolean> => {
			if (
				messageListLoading ||
				(selection.query_mode === 'metrics'
					? metricModels.unavailable != null
					: semanticReady !== true)
			)
				return false;
			setNotice(null);
			const sendEpoch = viewEpochRef.current;
			setSubmitting(true);
			let conversationId = activeConvId;
			if (!conversationId) {
				try {
					const created = await conversationsApi.create(
						text.slice(0, 50) || 'New conversation',
					);
					if (sendEpoch !== viewEpochRef.current) return false;
					conversationId = created.id;
					loadedFocusRef.current = conversationId;
					setActiveConvId(conversationId);
					updateFocusInUrl(conversationId);
					void refreshConversations().catch(() =>
						setHistoryError('Conversation history could not be loaded.'),
					);
				} catch {
					if (sendEpoch !== viewEpochRef.current) return false;
					setSubmitting(false);
					setHistoryError(
						'The conversation could not be created. Your question has been kept.',
					);
					return false;
				}
			}
			try {
				return await sendMessage(text, conversationId, selection);
			} finally {
				if (sendEpoch === viewEpochRef.current) setSubmitting(false);
			}
		},
		[
			activeConvId,
			sendMessage,
			refreshConversations,
			updateFocusInUrl,
			selection,
			metricModels.unavailable,
			semanticReady,
			messageListLoading,
		],
	);

	const selectClarificationMetric = useCallback(
		(context: MetricsQueryContext, metricId: string) => {
			if (isLoading) return;
			const next = selectionFromContext(context);
			if (!next.metric_context) return;
			setSelection({
				...next,
				metric_selection_explicit: true,
				metric_context: { ...next.metric_context, selected_metric_ids: [metricId] },
			});
			setNotice(`Selected ${metricId}. Reply below to continue with this definition.`);
		},
		[isLoading],
	);

	const exploreBlocked =
		selection.query_mode === 'explore' && semanticReady !== true && !messageListLoading;
	return (
		<div className="flex h-full bg-white dark:bg-zinc-950">
			<ChatSidebar
				conversations={conversations}
				activeId={activeConvId}
				onSelect={handleSelectConversation}
				onNewChat={handleNewChat}
				onRename={handleRename}
				onDelete={handleDelete}
				isOpen={sidebarOpen}
				onToggle={() => setSidebarOpen((open) => !open)}
				sidebarLoading={sidebarLoading}
			/>
			<main className="flex min-w-0 flex-1 flex-col">
				<ChatModeControls
					selection={selection}
					onChange={(next) => {
						setSelection(next);
						setNotice(null);
					}}
					metrics={metricModels}
					disabled={isLoading || submitting || messageListLoading}
					canReadMetrics={canReadMetrics}
				/>
				{(historyError || requestError) && (
					<p
						role="alert"
						className="border-b border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-200"
					>
						{requestError || historyError}
					</p>
				)}
				{notice && (
					<p
						role="status"
						className="px-4 py-2 text-xs text-[#4d7a00] dark:text-[#a3d63a]"
					>
						{notice}
					</p>
				)}
				{exploreBlocked ? (
					semanticReady == null ? (
						<div className="flex flex-1 items-center justify-center">
							<Spinner aria-label="Checking semantic layer" className="h-10 w-10" />
						</div>
					) : (
						<SemanticNotReady />
					)
				) : (
					<>
						<MessageList
							messages={messages}
							isLoading={isLoading}
							steps={steps}
							liveSql={liveSql}
							messageListLoading={messageListLoading}
							queryMode={selection.query_mode}
							onSelectMetric={selectClarificationMetric}
						/>
						<ChatInput
							onSend={handleSend}
							onStop={() => {
								viewEpochRef.current += 1;
								setSubmitting(false);
								stopGeneration();
							}}
							isLoading={isLoading || submitting}
							disabled={
								messageListLoading ||
								(selection.query_mode === 'metrics' &&
									metricModels.unavailable != null)
							}
							placeholder={
								selection.query_mode === 'metrics'
									? 'Ask about certified metrics or continue a clarification…'
									: 'Ask a Question…'
							}
						/>
					</>
				)}
			</main>
		</div>
	);
};
