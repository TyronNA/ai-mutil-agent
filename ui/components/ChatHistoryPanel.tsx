"use client";

import { MessageSquare, Plus, Trash2 } from "lucide-react";

export interface ChatHistoryItem {
  chatId: string;
  title: string;
  updatedAt: string;
  historyLength: number;
}

interface ChatHistoryPanelProps {
  items: ChatHistoryItem[];
  activeChatId?: string;
  onSelect: (chatId: string) => void;
  onDelete: (chatId: string) => void;
  onStartNew: () => void;
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.max(1, Math.floor(diff))}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function ChatHistoryPanel({ items, activeChatId, onSelect, onDelete, onStartNew }: ChatHistoryPanelProps) {
  return (
    <div className="space-y-2">
      <button
        onClick={onStartNew}
        className="flex w-full items-center justify-center gap-1.5 rounded-md border border-border bg-muted/30 px-2.5 py-2 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
      >
        <Plus className="h-3.5 w-3.5" />
        New Chat
      </button>

      {items.length === 0 ? (
        <p className="px-1 text-[11px] text-muted-foreground italic">No chat history yet.</p>
      ) : (
        <div className="space-y-1.5">
          {items.map((item) => (
            <div
              key={item.chatId}
              className={`rounded-md border px-2.5 py-2 transition-colors ${
                activeChatId === item.chatId
                  ? "border-primary/40 bg-primary/10"
                  : "border-border/50 bg-muted/20 hover:bg-muted/30"
              }`}
            >
              <div className="flex items-start gap-2">
                <button
                  onClick={() => onSelect(item.chatId)}
                  className="flex min-w-0 flex-1 items-start gap-1.5 text-left"
                >
                  <MessageSquare className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[11px] font-medium text-foreground">{item.title}</p>
                    <p className="mt-0.5 text-[10px] text-muted-foreground">
                      {timeAgo(item.updatedAt)} · {item.historyLength} msg
                    </p>
                  </div>
                </button>

                <button
                  onClick={() => onDelete(item.chatId)}
                  className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-red-400 transition-colors"
                  title="Delete chat history"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}