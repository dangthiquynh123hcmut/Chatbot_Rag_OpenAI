import { useRef, useState, type DragEvent } from "react";
import { api, errorMessage } from "../api.ts";
import type { DocumentInfo, DocumentStatus } from "../types.ts";

const STATUS_LABEL: Record<DocumentStatus, string> = {
  processing: "Đang xử lý",
  ready: "Sẵn sàng",
  error: "Lỗi",
};

// meta can be `{}` when metadata extraction failed (e.g. an ingest error), so every field is optional here.
function byline(meta: DocumentInfo["meta"]): string {
  const authors = meta?.authors ?? [];
  const names = authors.slice(0, 3).join(", ") + (authors.length > 3 ? " et al." : "");
  return [names, meta?.year].filter(Boolean).join(" · ");
}

interface DocumentPanelProps {
  documents: DocumentInfo[];
  selected: string[];
  onToggle: (id: string) => void;
  onRefresh: () => Promise<void>;
  onOpen: (doc: DocumentInfo) => void;
  isAdmin: boolean;
}

export default function DocumentPanel({
  documents,
  selected,
  onToggle,
  onRefresh,
  onOpen,
  isAdmin,
}: DocumentPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);

  async function upload(files: FileList | null) {
    const list = Array.from(files ?? []).filter((f) => /\.(pdf|docx)$/i.test(f.name));
    if (!list.length) {
      setError("Chỉ nhận file PDF hoặc DOCX.");
      return;
    }
    setError("");
    setUploading(true);
    try {
      await api.uploadDocuments(list);
      await onRefresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function runAction(action: () => Promise<unknown>) {
    try {
      await action();
      await onRefresh();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  function remove(doc: DocumentInfo) {
    if (!window.confirm(`Xóa tài liệu "${doc.title}" và toàn bộ dữ liệu đã lập chỉ mục?`)) return;
    void runAction(() => api.deleteDocument(doc.id));
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    void upload(e.dataTransfer.files);
  }

  return (
    <aside className="panel docs">
      <h2>Tài liệu</h2>
      {isAdmin && (
        <div
          className={`dropzone ${dragOver ? "over" : ""}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          {uploading ? "Đang tải lên…" : "Kéo thả hoặc bấm để chọn PDF / DOCX"}
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.docx"
            hidden
            onChange={(e) => void upload(e.target.files)}
          />
        </div>
      )}
      {error && <div className="error">{error}</div>}
      <p className="hint">
        {selected.length ? `Đang lọc theo ${selected.length} tài liệu` : "Tìm trên tất cả tài liệu"}
      </p>
      <ul className="doc-list">
        {documents.map((d) => (
          <li key={d.id} className={`doc ${d.status}`}>
            <label className="doc-main">
              <input
                type="checkbox"
                disabled={d.status !== "ready"}
                checked={selected.includes(d.id)}
                onChange={() => onToggle(d.id)}
              />
              <span className="doc-title" title={d.filename}>
                {d.title}
              </span>
            </label>
            {byline(d.meta) && <div className="doc-byline">{byline(d.meta)}</div>}
            <div className="doc-meta">
              <span className={`badge ${d.status}`}>{STATUS_LABEL[d.status] ?? d.status}</span>
              {d.status === "ready" && (
                <span>
                  {d.num_sections} mục · {d.num_chunks} chunk
                </span>
              )}
            </div>
            {d.status === "error" && <div className="error small">{d.error}</div>}
            <div className="doc-actions">
              <button disabled={d.status !== "ready"} onClick={() => onOpen(d)}>
                Xem cấu trúc
              </button>
              {isAdmin && (
                <>
                  <button
                    disabled={d.status === "processing"}
                    onClick={() => void runAction(() => api.reindexDocument(d.id))}
                  >
                    Xử lý lại
                  </button>
                  <button className="danger" onClick={() => remove(d)}>
                    Xóa
                  </button>
                </>
              )}
            </div>
          </li>
        ))}
        {!documents.length && <li className="empty">Chưa có tài liệu nào.</li>}
      </ul>
    </aside>
  );
}
