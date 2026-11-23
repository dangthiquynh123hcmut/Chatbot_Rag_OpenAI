import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, onUnauthorized, refreshSession, setToken } from "./api.ts";
import ChatSidebar from "./components/ChatSidebar.tsx";
import ChatView from "./components/ChatView.tsx";
import DocumentPanel from "./components/DocumentPanel.tsx";
import DocumentViewer from "./components/DocumentViewer.tsx";
import LoginPage from "./components/LoginPage.tsx";
import type { AuthResponse, ChatMessage, Conversation, DocumentInfo, HealthInfo, User } from "./types.ts";

function upsertConversation(list: Conversation[], conv: Conversation): Conversation[] {
  return [conv, ...list.filter((c) => c.id !== conv.id)];
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [notice, setNotice] = useState("");

  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [viewing, setViewing] = useState<DocumentInfo | null>(null);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [error, setError] = useState("");

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingConv, setLoadingConv] = useState(false);
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState("");
  const activeRef = useRef<string | null>(null);

  const resetSession = useCallback(() => {
    setUser(null);
    setDocuments([]);
    setSelected([]);
    setConversations([]);
    setActiveId(null);
    activeRef.current = null;
    setMessages([]);
    setViewing(null);
  }, []);

  useEffect(() => {
    onUnauthorized(() => {
      setToken(null);
      resetSession();
      setNotice("Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.");
    });
    return () => onUnauthorized(null);
  }, [resetSession]);

  useEffect(() => {
    refreshSession()
      .then((auth) => {
        if (auth) setUser(auth.user);
      })
      .finally(() => setAuthChecked(true));
  }, []);

  const refreshDocuments = useCallback(async () => {
    try {
      const r = await api.listDocuments();
      setDocuments(r.documents);
      setSelected((sel) => sel.filter((id) => r.documents.some((d) => d.id === id && d.status === "ready")));
      setError("");
    } catch (e) {
      setError(`Không tải được danh sách tài liệu: ${errorMessage(e)}`);
    }
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations((await api.listConversations()).conversations);
    } catch (e) {
      setChatError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    void refreshDocuments();
    void refreshConversations();
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, [user, refreshDocuments, refreshConversations]);

  useEffect(() => {
    if (!user || !documents.some((d) => d.status === "processing")) return;
    const t = window.setInterval(() => void refreshDocuments(), 3000);
    return () => window.clearInterval(t);
  }, [user, documents, refreshDocuments]);

  function onAuthenticated(auth: AuthResponse) {
    setToken(auth.access_token);
    setNotice("");
    setUser(auth.user);
  }

  function logout() {
    void api
      .logout()
      .catch(() => undefined)
      .finally(() => {
        setToken(null);
        resetSession();
        setNotice("");
      });
  }

  async function selectConversation(id: string) {
    setActiveId(id);
    activeRef.current = id;
    setChatError("");
    setLoadingConv(true);
    setMessages([]);
    try {
      const detail = await api.getConversation(id);
      if (activeRef.current === id) setMessages(detail.messages);
    } catch (e) {
      setChatError(errorMessage(e));
    } finally {
      if (activeRef.current === id) setLoadingConv(false);
    }
  }

  function newConversation() {
    setActiveId(null);
    activeRef.current = null;
    setMessages([]);
    setChatError("");
  }

  async function sendMessage(text: string): Promise<boolean> {
    setChatError("");
    setSending(true);
    const tempId = `pending-${Date.now()}`;
    const optimistic: ChatMessage = {
      id: tempId,
      role: "user",
      content: text,
      result: null,
      document_ids: selected,
      created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, optimistic]);
    try {
      let convId = activeRef.current;
      if (!convId) {
        const conv = await api.createConversation();
        convId = conv.id;
        activeRef.current = convId;
        setActiveId(convId);
        setConversations((list) => upsertConversation(list, conv));
      }
      const res = await api.sendMessage(convId, text, selected);
      setConversations((list) => upsertConversation(list, res.conversation));
      if (activeRef.current === convId) {
        setMessages((m) => [...m.filter((x) => x.id !== tempId), res.user_message, res.assistant_message]);
      }
      return true;
    } catch (e) {
      setMessages((m) => m.filter((x) => x.id !== tempId));
      setChatError(errorMessage(e));
      return false;
    } finally {
      setSending(false);
    }
  }

  async function renameConversation(conv: Conversation) {
    const title = window.prompt("Tên mới cho cuộc hội thoại", conv.title)?.trim();
    if (!title || title === conv.title) return;
    try {
      const updated = await api.renameConversation(conv.id, title);
      setConversations((list) => list.map((c) => (c.id === conv.id ? { ...c, ...updated } : c)));
    } catch (e) {
      setChatError(errorMessage(e));
    }
  }

  async function deleteConversation(conv: Conversation) {
    if (!window.confirm(`Xóa cuộc hội thoại "${conv.title}"? Không thể khôi phục.`)) return;
    try {
      await api.deleteConversation(conv.id);
      setConversations((list) => list.filter((c) => c.id !== conv.id));
      if (activeRef.current === conv.id) newConversation();
    } catch (e) {
      setChatError(errorMessage(e));
    }
  }

  const toggle = (id: string) =>
    setSelected((sel) => (sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]));

  const closeViewer = useCallback(() => setViewing(null), []);

  if (!authChecked) return <div className="login-page hint">Đang tải…</div>;
  if (!user) return <LoginPage onAuthenticated={onAuthenticated} notice={notice} />;

  return (
    <div className="app">
      <header className="topbar">
        <h1>Tra cứu tài liệu</h1>
        <span className="sub">
          {health &&
            `${health.provider === "bedrock" ? `Bedrock (${health.aws_region ?? ""})` : "OpenAI"} · ${
              health.llm_model
            } · rerank: ${health.reranker}`}
        </span>
        <div className="user-box">
          <span className="user-name">
            {user.display_name || user.username}
            {user.role === "admin" && <span className="badge ready">admin</span>}
          </span>
          <button onClick={logout}>Đăng xuất</button>
        </div>
      </header>
      {error && <div className="error banner">{error}</div>}
      <div className="layout">
        <div className="sidebar">
          <ChatSidebar
            conversations={conversations}
            activeId={activeId}
            onSelect={(id) => void selectConversation(id)}
            onNew={newConversation}
            onRename={(c) => void renameConversation(c)}
            onDelete={(c) => void deleteConversation(c)}
          />
          <DocumentPanel
            documents={documents}
            selected={selected}
            onToggle={toggle}
            onRefresh={refreshDocuments}
            onOpen={setViewing}
            isAdmin={user.role === "admin"}
          />
        </div>
        <ChatView
          messages={messages}
          loading={loadingConv}
          sending={sending}
          error={chatError}
          filterCount={selected.length}
          onSend={sendMessage}
        />
      </div>
      {viewing && (
        <DocumentViewer
          doc={viewing}
          isAdmin={user.role === "admin"}
          onClose={closeViewer}
          onUpdated={(d) => {
            setViewing(d);
            setDocuments((list) => list.map((x) => (x.id === d.id ? d : x)));
          }}
        />
      )}
    </div>
  );
}
