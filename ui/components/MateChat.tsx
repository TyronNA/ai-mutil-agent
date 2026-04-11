"use client";

import { useCallback, useRef, useState } from "react";
import { Send, Loader2, Trash2, Zap, Brain } from "lucide-react";
import { useChatSession } from "@/hooks/useChatSession";
import type { ChatMessage } from "@/types";

interface MateChatProps {
  initialChatId?: string;
  initialHistory?: ChatMessage[];
  onHistoryChange?: (chatId: string | undefined, history: ChatMessage[]) => void;
}

export function MateChat({ initialChatId, initialHistory, onHistoryChange }: MateChatProps) {
  const [model, setModel] = useState<"flash" | "pro">("flash");
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const { history, loading, effectiveModel, downgraded, messagesContainerRef, sendMessage, clearChat } =
    useChatSession({ character: "mate", model, initialChatId, initialHistory, onHistoryChange });

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

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
        <span className="text-base">😄</span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-foreground">Mate</p>
          <p className="text-[10px] text-muted-foreground">Trò chuyện & brainstorm</p>
        </div>

        <div className="flex items-center rounded-md border border-border bg-muted/30 p-0.5 gap-0.5 flex-shrink-0">
          <button
            type="button"
            onClick={() => setModel("flash")}
            className={`flex items-center gap-1 rounded-sm px-2.5 py-1 text-[10px] font-semibold transition-all ${
              model === "flash" ? "bg-card shadow-sm text-foreground" : "text-muted-foreground hover:text-foreground"
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
              model === "pro" ? "bg-card shadow-sm text-foreground" : "text-muted-foreground hover:text-foreground"
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
            Pro was requested but backend downgraded to Flash.
          </p>
        )}
      </div>

      {/* Messages */}
      <div ref={messagesContainerRef} className="flex-1 overflow-y-auto p-3 space-y-3 [overscroll-behavior:contain] min-h-0">
        {history.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-2">
            <span className="text-4xl opacity-30">😄</span>
            <p className="text-xs text-muted-foreground max-w-xs">
              Chat với Mate để brainstorm nhanh. Khi chốt giải pháp, chuyển qua TechExpert để tạo task chuẩn pipeline.
            </p>
            <div className="w-full space-y-1.5 text-left">
              {[
                "Tôi muốn thêm hệ thống phần thưởng hàng ngày, nên làm thế nào?",
                "Giải thích tại sao cần dùng Context Cache cho game này?",
                "Review cách thiết kế UI này có ổn không?",
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
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[88%] rounded-xl px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap break-words ${
                msg.role === "user"
                  ? "bg-primary/20 text-foreground rounded-br-sm"
                  : "bg-muted/60 text-foreground rounded-bl-sm"
              }`}
            >
              {msg.role === "assistant" && (
                <span className="mb-1 block text-[10px] text-muted-foreground font-medium">
                  😄 Mate · {model === "pro" ? "Pro" : "Flash"}
                </span>
              )}
              {msg.content}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="rounded-xl rounded-bl-sm bg-muted/60 px-3 py-2 flex items-center gap-2">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-border p-3 flex-shrink-0">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Hỏi Mate… (Enter gửi)"
            rows={2}
            disabled={loading}
            className="flex-1 resize-none rounded-md border border-border bg-muted/50 px-2.5 py-2 text-sm text-foreground placeholder:text-muted-foreground/50 focus:border-primary/50 focus:outline-none focus:ring-1 focus:ring-primary/30 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="flex items-center justify-center rounded-md bg-primary/20 px-3 text-primary hover:bg-primary/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
