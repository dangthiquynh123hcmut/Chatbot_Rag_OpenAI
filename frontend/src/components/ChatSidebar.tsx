import type { Conversation } from "../types.ts";

interface ChatSidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (conversation: Conversation) => void;
  onDelete: (conversation: Conversation) => void;
}

function relativeDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export default function ChatSidebar({ conversations, activeId, onSelect, onNew, onRename, onDelete }: ChatSidebarProps) {
  return (
    <section className="panel history">
      <div className="history-head">
        <h2>Lịch sử hội thoại</h2>
        <button className="primary-button" onClick={onNew}>
          + Mới
        </button>
      </div>
      <ul className="conv-list">
        {conversations.map((c) => (
          <li key={c.id} className={c.id === activeId ? "active" : ""}>
            <button className="conv-main" onClick={() => onSelect(c.id)} title={c.title}>
              <span className="conv-title">{c.title}</span>
              <span className="conv-date">{relativeDate(c.updated_at)}</span>
            </button>
            <div className="conv-actions">
              <button onClick={() => onRename(c)} aria-label="Đổi tên" title="Đổi tên">
                ✎
              </button>
              <button className="danger" onClick={() => onDelete(c)} aria-label="Xóa" title="Xóa">
                ✕
              </button>
            </div>
          </li>
        ))}
        {!conversations.length && <li className="empty">Chưa có hội thoại nào.</li>}
      </ul>
    </section>
  );
}
