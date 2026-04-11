"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { X, Send, Loader2, Trash2, Zap, Brain, ClipboardList } from "lucide-react";
import { sendChat } from "@/lib/api";
import type { ChatCharacter, ChatMessage } from "@/types";

interface TechChatProps {
  onClose: () => void;
  onCreateTask?: (taskText: string) => void;
  initialChatId?: string;
  initialHistory?: ChatMessage[];
  onHistoryChange?: (chatId: string | undefined, history: ChatMessage[]) => void;
}

const CREATE_TASK_PROMPT =
  "Based on our conversation above, write a clear and concise task description (3-5 sentences max) " +
  "that can be given directly to the Dev pipeline to implement. " +
  "Output ONLY the task description text — no preamble, no markdown headers.";

export function TechChat({ onClose, onCreateTask, initialChatId, initialHistory, onHistoryChange }: TechChatProps) {
  const [chatId, setChatId] = useState<string | undefined>(initialChatId);
  const [history, setHistory] = useState<ChatMessage[]>(initialHistory ?? []);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [character, setCharacter] = useState<ChatCharacter>("tech_expert");
  const [model, setModel] = useState<"flash" | "pro">("flash");
  const [effectiveModel, setEffectiveModel] = useState<string>("");
  const [downgraded, setDowngraded] = useState(false);
  const [creatingTask, setCreatingTask] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const lastAssistantMsgCountRef = useRef(0);
  const scrollTimeoutRef = useRef<NodeJS.Timeout>(null);

  // Scroll to bottom ONLY when new assistant message arrives
  useEffect(() => {
    const assistantMsgCount = history.filter((m) => m.role === "assistant").length;
    
    // Clear any pending scroll
    if (scrollTimeoutRef.current) {
      clearTimeout(scrollTimeoutRef.current);
    }

    // Only scroll if new message arrived or stopped loading (but not on every history change)
    if (assistantMsgCount > lastAssistantMsgCountRef.current || (loading && assistantMsgCount > 0)) {
      lastAssistantMsgCountRef.current = assistantMsgCount;
      
      // On mobile, use instant scroll to avoid jumps with soft keyboard
      const isMobile = /android|webos|iphone|ipad|ipod|blackberry|iemobile|opera mini/i.test(
        navigator.userAgent.toLowerCase()
      );
      
      scrollTimeoutRef.current = setTimeout(() => {
        bottomRef.current?.scrollIntoView({ behavior: isMobile ? "auto" : "smooth" });
      }, 50);
    }
  }, [history, loading]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    if (initialChatId !== chatId || initialHistory !== history) {
      setChatId(initialChatId);
      setHistory(initialHistory ?? []);
    }
  }, [initialChatId, initialHistory, chatId, history]);

  const sendMessage = useCallback(async (msg: string, silent = false) => {
    if (!msg || loading) return;
    setLoading(true);
    if (!silent) setHistory((prev) => [...prev, { role: "user", content: msg }]);
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
    } finally {
      setLoading(false);
    }
  }, [loading, chatId, character, model]);

  const handleSend = useCallback(async () => {
    const msg = input.trim();
    if (!msg) return;
    setInput("");
    await sendMessage(msg);
  }, [input, sendMessage]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleCreateTask = useCallback(async () => {
    if (history.length === 0 || creatingTask) return;
    setCreatingTask(true);
    try {
      const taskText = await sendMessage(CREATE_TASK_PROMPT);
      if (taskText && onCreateTask) {
        onCreateTask(taskText.trim());
      }
    } finally {
      setCreatingTask(false);
    }
  }, [history, creatingTask, sendMessage, onCreateTask]);

  const clearChat = () => {
    setHistory([]);
    setChatId(undefined);
    setEffectiveModel("");
    setDowngraded(false);
    lastAssistantMsgCountRef.current = 0;
    onHistoryChange?.(undefined, []);
  };

  useEffect(() => {
    return () => {
      if (scrollTimeoutRef.current) {
        clearTimeout(scrollTimeoutRef.current);
      }
    };
  }, []);

  const hasConversation = history.some((m) => m.role === "user");
  const characterLabel = character === "mate" ? "Mate" : "TechExpert";
  const characterIcon = character === "mate" ? "😄" : "🏛";

  const switchCharacter = (next: ChatCharacter) => {
    if (next === character) return;
    setCharacter(next);
    setModel("flash");
    setHistory([]);
    setChatId(undefined);
    setEffectiveModel("");
    setDowngraded(false);
    onHistoryChange?.(undefined, []);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
        <span className="text-base">{characterIcon}</span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-foreground truncate">{characterLabel}</p>
        </div>

        <div className="flex items-center rounded-md border border-border bg-muted/30 p-0.5 gap-0.5 flex-shrink-0">
          <button
            type="button"
            onClick={() => switchCharacter("tech_expert")}
            className={`flex items-center gap-1 rounded-sm px-2.5 py-1 text-[10px] font-semibold transition-all ${
              character === "tech_expert"
                ? "bg-card shadow-sm text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            title="Kiến trúc sư kỹ thuật cho Mộng Võ Lâm"
          >
            🏛 Tech
          </button>
          <button
            type="button"
            onClick={() => switchCharacter("mate")}
            className={`flex items-center gap-1 rounded-sm px-2.5 py-1 text-[10px] font-semibold transition-all ${
              character === "mate"
                ? "bg-card shadow-sm text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            title="Trợ lý ảo Mate: dí dỏm, ngắn gọn, nhưng chính xác"
          >
            😄 Mate
          </button>
        </div>

        {/* Model toggle */}
        <div className="flex items-center rounded-md border border-border bg-muted/30 p-0.5 gap-0.5 flex-shrink-0">
          <button
            type="button"
            onClick={() => setModel("flash")}
            className={`flex items-center gap-1 rounded-sm px-2.5 py-1 text-[10px] font-semibold transition-all ${
              model === "flash"
                ? "bg-card shadow-sm text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            title="Gemini Flash — fast & cheap"
          >
            <Zap className="h-2.5 w-2.5 text-yellow-400" />
            Flash
          </button>
          <button
            type="button"
            onClick={() => setModel("pro")}
            className={`flex items-center gap-1 rounded-sm px-2.5 py-1 text-[10px] font-semibold transition-all ${
              model === "pro"
                ? "bg-card shadow-sm text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
            title="Gemini Pro — deeper reasoning"
          >
            <Brain className="h-2.5 w-2.5 text-purple-400" />
            Pro
          </button>
        </div>

        <button
          onClick={clearChat}
          className="rounded p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          title="Clear conversation"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={onClose}
          className="rounded p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="border-b border-border/70 px-3 py-1.5">
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-muted-foreground">
            Requested: <span className="font-mono text-foreground">{model}</span>
          </span>
          <span className="text-muted-foreground">
            Effective: <span className="font-mono text-foreground">{effectiveModel || "(pending)"}</span>
          </span>
        </div>
        {downgraded && (
          <p className="mt-1 text-[10px] text-amber-400">
            Pro was requested but backend downgraded to Flash. Check PRO_MODEL / location / access.
          </p>
        )}
      </div>

      {/* Messages */}
      <div ref={messagesContainerRef} className="flex-1 overflow-y-auto p-3 space-y-3 [overscroll-behavior:contain] [@supports(height:100dvh)]:h-[100dvh]">
        {history.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-2">
            <span className="text-4xl opacity-30">{characterIcon}</span>
            <p className="text-xs text-muted-foreground max-w-xs">
              {character === "mate"
                ? "Chat với Mate để brainstorm nhanh. Khi chốt giải pháp, chuyển qua TechExpert để tạo task chuẩn pipeline."
                : "Discuss the feature with TechExpert first, then hit "}
              {character !== "mate" && <strong>Create Task</strong>}
              {character !== "mate" && " to auto-generate a task description for the pipeline."}
            </p>
            <div className="w-full space-y-1.5 text-left">
              {[
                "Tôi muốn thêm hệ thống phần thưởng hàng ngày, nên làm thế nào?",
                "Tại sao CombatEngine không được import Phaser?",
                "Review cách implement SaveManager này",
              ].map((q) => (
                <button
                  key={q}
                  onClick={() => setInput(q)}
                  className="block w-full rounded border border-border bg-muted/30 px-2.5 py-1.5 text-left text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {history.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[88%] rounded-xl px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap break-words ${
                msg.role === "user"
                  ? "bg-primary/20 text-foreground rounded-br-sm"
                  : "bg-muted/60 text-foreground rounded-bl-sm"
              }`}
            >
              {msg.role === "assistant" && (
                <span className="mb-1 block text-[10px] text-muted-foreground font-medium">
                  {characterIcon} {characterLabel} · {model === "pro" ? "Pro" : "Flash"}
                </span>
              )}
              {msg.content}
            </div>
          </div>
        ))}

        {(loading || creatingTask) && (
          <div className="flex justify-start">
            <div className="rounded-xl rounded-bl-sm bg-muted/60 px-3 py-2 flex items-center gap-2">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
              {creatingTask && (
                <span className="text-xs text-muted-foreground">Generating task…</span>
              )}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Create Task banner — shown when there's a conversation */}
      {hasConversation && onCreateTask && (
        <div className="border-t border-border px-3 py-2 flex-shrink-0">
          <button
            onClick={handleCreateTask}
            disabled={loading || creatingTask}
            className="flex w-full items-center justify-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {creatingTask ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ClipboardList className="h-3.5 w-3.5" />
            )}
            Create Task from this Chat
          </button>
        </div>
      )}

      {/* Input */}
      <div className="border-t border-border p-3 flex-shrink-0 [overscroll-behavior:contain]">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Hỏi ${characterLabel}… (Enter gửi)`}
            rows={2}
            disabled={loading || creatingTask}
            className="flex-1 resize-none rounded-md border border-border bg-muted/50 px-2.5 py-2 text-sm text-foreground placeholder:text-muted-foreground/50 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={loading || creatingTask || !input.trim()}
            className="flex items-center justify-center rounded-md bg-primary/20 px-3 text-primary hover:bg-primary/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
        {chatId && (
          <p className="mt-1 text-[10px] text-muted-foreground/40 font-mono">
            {chatId} · {history.filter((m) => m.role === "user").length} turn(s)
          </p>
        )}
      </div>
    </div>
  );
}
