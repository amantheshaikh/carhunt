"use client";

import { useState, useRef, useEffect } from "react";

interface Message {
  role: "user" | "assistant";
  content: string;
  loading?: boolean;
}

const SUGGESTIONS = [
  "Which cars did I dismiss under $24k?",
  "What's the highest-scored listing I haven't contacted?",
  "Show me all new cars in the pipeline",
  "Which dealers have I contacted?",
];

export default function QueryChat() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const ask = async (question: string) => {
    if (!question.trim() || loading) return;
    setInput("");
    const newMessages: Message[] = [
      ...messages,
      { role: "user", content: question }
    ];
    setMessages([
      ...newMessages,
      { role: "assistant", content: "", loading: true },
    ]);
    setLoading(true);

    try {
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history: newMessages }),
      });
      const data = await res.json();
      setMessages((prev) => [
        ...prev.slice(0, -1),
        { role: "assistant", content: data.answer || data.error || "No answer returned." },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev.slice(0, -1),
        { role: "assistant", content: "Something went wrong. Check the console." },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {/* Floating trigger button */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="fixed bottom-5 right-5 z-50 flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-full shadow-lg transition-colors"
      >
        <span className="text-base leading-none">?</span>
        Ask about listings
      </button>

      {/* Chat panel */}
      {open && (
        <div className="fixed bottom-16 right-5 z-50 w-96 max-h-[70vh] flex flex-col bg-zinc-900 border border-zinc-700 rounded-2xl shadow-2xl overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
            <span className="text-sm font-medium text-zinc-200">Ask about your listings</span>
            <button
              onClick={() => setOpen(false)}
              className="text-zinc-500 hover:text-zinc-300 text-lg leading-none"
            >
              ×
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0">
            {messages.length === 0 ? (
              <div className="space-y-2">
                <p className="text-xs text-zinc-500">Try asking:</p>
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => ask(s)}
                    className="block w-full text-left px-3 py-2 text-xs text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            ) : (
              messages.map((m, i) => (
                <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[85%] px-3 py-2 rounded-xl text-sm leading-relaxed ${
                      m.role === "user"
                        ? "bg-blue-700 text-white"
                        : "bg-zinc-800 text-zinc-200"
                    }`}
                  >
                    {m.loading ? (
                      <span className="inline-flex gap-1">
                        <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                        <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                        <span className="w-1.5 h-1.5 bg-zinc-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                      </span>
                    ) : (
                      <span style={{ whiteSpace: "pre-wrap" }}>{m.content}</span>
                    )}
                  </div>
                </div>
              ))
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div className="p-3 border-t border-zinc-800">
            <form
              onSubmit={(e) => { e.preventDefault(); ask(input); }}
              className="flex gap-2"
            >
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="e.g. Which Toyotas have I not contacted yet?"
                disabled={loading}
                className="flex-1 px-3 py-2 text-sm bg-zinc-800 border border-zinc-700 rounded-lg text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-zinc-500 disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={loading || !input.trim()}
                className="px-3 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-40 transition-colors"
              >
                Ask
              </button>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
