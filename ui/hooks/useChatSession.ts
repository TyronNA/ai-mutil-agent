"use client";

import { flushSync } from "react-dom";
import { useCallback, useEffect, useRef, useState } from "react";
import { sendChat } from "@/lib/api";
import type { ChatCharacter, ChatMessage } from "@/types";

export interface UseChatSessionOptions {
  character: ChatCharacter;
  model: "flash" | "pro";
  initialChatId?: string;
  initialHistory?: ChatMessage[];
  onHistoryChange?: (chatId: string | undefined, history: ChatMessage[]) => void;
}

export function useChatSession({
  character,
  model,
  initialChatId,
  initialHistory,
  onHistoryChange,
}: UseChatSessionOptions) {
  const [chatId, setChatId] = useState<string | undefined>(initialChatId);
  const [history, setHistory] = useState<ChatMessage[]>(initialHistory ?? []);
  const [loading, setLoading] = useState(false);
  const [effectiveModel, setEffectiveModel] = useState("");
  const [downgraded, setDowngraded] = useState(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const lastAssistantMsgCountRef = useRef(0);

  const scrollToBottom = useCallback(() => {
    const el = messagesContainerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // Only sync when parent selects a different chat thread — NOT on internal history changes
  useEffect(() => {
    setChatId(initialChatId);
    setHistory(initialHistory ?? []);
    lastAssistantMsgCountRef.current = (initialHistory ?? []).filter(
      (m) => m.role === "assistant",
    ).length;
  }, [initialChatId, initialHistory]); // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll when new assistant message arrives
  useEffect(() => {
    const count = history.filter((m) => m.role === "assistant").length;
    if (count > lastAssistantMsgCountRef.current) {
      lastAssistantMsgCountRef.current = count;
      scrollToBottom();
    }
  }, [history, scrollToBottom]);

  const sendMessage = useCallback(
    async (msg: string, silent = false): Promise<string> => {
      if (!msg || loading) return "";
      setLoading(true);
      if (!silent) {
        flushSync(() => {
          setHistory((prev) => [...prev, { role: "user", content: msg }]);
        });
        scrollToBottom();
      }
      try {
        const res = await sendChat(msg, chatId, character, model);
        setChatId(res.chat_id);
        setHistory(res.history);
        setEffectiveModel(res.effective_model ?? "");
        setDowngraded(Boolean(res.downgraded_to_flash));
        onHistoryChange?.(res.chat_id, res.history);
        return res.history[res.history.length - 1]?.content ?? "";
      } catch {
        setHistory((prev) => [
          ...prev,
          {
            role: "assistant",
            content:
              character === "mate"
                ? "⚠️ Error — chưa gọi được Mate. Kiểm tra backend giúp mình nhé."
                : "⚠️ Error — could not reach TechExpert. Check the server.",
          },
        ]);
        return "";
      } finally {
        setLoading(false);
      }
    },
    [loading, chatId, character, model, onHistoryChange, scrollToBottom],
  );

  const clearChat = useCallback(() => {
    setHistory([]);
    setChatId(undefined);
    setEffectiveModel("");
    setDowngraded(false);
    lastAssistantMsgCountRef.current = 0;
    onHistoryChange?.(undefined, []);
  }, [onHistoryChange]);

  return {
    chatId,
    history,
    loading,
    effectiveModel,
    downgraded,
    messagesContainerRef,
    sendMessage,
    clearChat,
  };
}
