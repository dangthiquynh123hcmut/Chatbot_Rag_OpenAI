import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { ChatMessage, QueryResult } from "../types.ts";
import ResultView from "./ResultView.tsx";

const EXAMPLES: readonly string[] = [
  "Summarize Chapter 1",
  "What does section 3.2 explain?",
  "What are the main contributions of the paper?",
];

interface ChatViewProps {
  messages: ChatMessage[];
  loading: boolean;
  sending: boolean;
  error: string;
  filterCount: number;
  onSend: (text: string) => Promise<boolean>;
}

function isQueryResult(value: ChatMessage["result"]): value is QueryResult {
  return !!value && typeof value === "object" && "answer" in value && "sources" in value;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}

function AssistantMessage({ message }: { message: ChatMessage }) {
  if (isQueryResult(message.result)) return <ResultView result={message.result} />;
  return <div className="error">{message.content}</div>;
}

export default function ChatView({ messages, loading, sending, error, filterCount, onSend }: ChatViewProps) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, sending]);

  async function submit(text: string) {
    const q = text.trim();
    if (q.length < 2 || sending) return;
    setDraft("");
    const ok = await onSend(q);
    if (!ok) setDraft(q);
  }

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    void submit(draft);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit(draft);
    }
  }

  return (
    <main className="panel chat">
      <div className="thread">
        {loading && <p className="hint">Đang tải hội thoại…</p>}
        {!loading && messages.length === 0 && (
          <div className="empty-chat">
            <h2>Hỏi về nội dung tài liệu</h2>
            <p className="hint">Hệ thống trả về đúng chương/mục kèm trích dẫn nguyên văn. Nhắc tên tài liệu, tác giả ("Vaswani et al.") hoặc DOI/ISBN để chỉ tìm trong tài liệu đó.</p>
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} onClick={() => void submit(ex)} disabled={sending}>
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <div className="msg-meta">
              {m.role === "user" ? "Bạn" : "Trợ lý"} · {formatTime(m.created_at)}
            </div>
            {m.role === "user" ? <div className="bubble">{m.content}</div> : <AssistantMessage message={m} />}
          </div>
        ))}
        {sending && (
          <div className="msg assistant">
            <div className="msg-meta">Trợ lý</div>
            <div className="typing">Đang tra cứu tài liệu…</div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {error && <div className="error">{error}</div>}

      <form className="ask" onSubmit={onSubmit}>
        <textarea
          value={draft}
          placeholder={
            filterCount
              ? `Hỏi trong ${filterCount} tài liệu đã chọn… (Enter để gửi, Shift+Enter xuống dòng)`
              : "Ask a question, e.g. In Clean Architecture, what is the Dependency Rule? (Enter để gửi)"
          }
          rows={2}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={sending}
        />
        <button type="submit" disabled={sending || draft.trim().length < 2}>
          {sending ? "Đang tìm…" : "Gửi"}
        </button>
      </form>
    </main>
  );
}
