import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "./api.ts";
import DocumentPanel from "./components/DocumentPanel.tsx";
import DocumentViewer from "./components/DocumentViewer.tsx";
import QueryPanel from "./components/QueryPanel.tsx";
import type { DocumentInfo, HealthInfo } from "./types.ts";

export default function App() {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [viewing, setViewing] = useState<DocumentInfo | null>(null);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const r = await api.listDocuments();
      setDocuments(r.documents);
      setSelected((sel) => sel.filter((id) => r.documents.some((d) => d.id === id && d.status === "ready")));
      setError("");
    } catch (e) {
      setError(`Không kết nối được backend: ${errorMessage(e)}`);
    }
  }, []);

  useEffect(() => {
    void refresh();
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, [refresh]);

  useEffect(() => {
    if (!documents.some((d) => d.status === "processing")) return;
    const t = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(t);
  }, [documents, refresh]);

  const toggle = (id: string) =>
    setSelected((sel) => (sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]));

  const closeViewer = useCallback(() => setViewing(null), []);

  return (
    <div className="app">
      <header className="topbar">
        <h1>Tra cứu tài liệu tiêu chuẩn &amp; văn bản luật</h1>
        <span className="sub">
          Hierarchy-aware RAG · PostgreSQL pgvector · Rerank
          {health &&
            ` · ${health.provider === "bedrock" ? `Bedrock (${health.aws_region ?? ""})` : "OpenAI"} · ${
              health.llm_model
            } · rerank: ${health.reranker}`}
        </span>
      </header>
      {error && <div className="error banner">{error}</div>}
      <div className="layout">
        <DocumentPanel
          documents={documents}
          selected={selected}
          onToggle={toggle}
          onRefresh={refresh}
          onOpen={setViewing}
        />
        <QueryPanel selected={selected} />
      </div>
      {viewing && <DocumentViewer doc={viewing} onClose={closeViewer} />}
    </div>
  );
}
