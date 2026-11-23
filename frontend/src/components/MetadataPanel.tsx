import { useState, type FormEvent } from "react";
import { api, errorMessage } from "../api.ts";
import type { DocType, DocumentInfo, DocumentMeta } from "../types.ts";

const DOC_TYPES: readonly DocType[] = ["paper", "book", "report", "manual", "other"];

interface MetadataPanelProps {
  doc: DocumentInfo;
  isAdmin: boolean;
  onSaved: (doc: DocumentInfo) => void;
}

interface FormState {
  title: string;
  short_title: string;
  authors: string;
  year: string;
  doc_type: DocType;
  publisher: string;
  doi: string;
  arxiv_id: string;
  isbn: string;
  aliases: string;
  summary: string;
}

function toForm(meta: DocumentMeta | undefined, fallbackTitle: string): FormState {
  return {
    title: meta?.title || fallbackTitle,
    short_title: meta?.short_title ?? "",
    authors: (meta?.authors ?? []).join("; "),
    year: meta?.year ? String(meta.year) : "",
    doc_type: meta?.doc_type ?? "other",
    publisher: meta?.publisher ?? "",
    doi: meta?.doi ?? "",
    arxiv_id: meta?.arxiv_id ?? "",
    isbn: meta?.isbn ?? "",
    aliases: (meta?.aliases ?? []).join("; "),
    summary: meta?.summary ?? "",
  };
}

function splitList(value: string): string[] {
  return value
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
}

function Row({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="meta-row">
      <span className="meta-label">{label}</span>
      <span>{value}</span>
    </div>
  );
}

export default function MetadataPanel({ doc, isAdmin, onSaved }: MetadataPanelProps) {
  const meta = doc.meta;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<FormState>(() => toForm(meta, doc.title));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    const year = form.year.trim() ? Number(form.year) : null;
    if (year !== null && (!Number.isInteger(year) || year < 1400 || year > 2100)) {
      setBusy(false);
      setError("Năm không hợp lệ.");
      return;
    }
    try {
      const res = await api.updateMetadata(doc.id, {
        title: form.title.trim(),
        short_title: form.short_title.trim(),
        authors: splitList(form.authors),
        year,
        doc_type: form.doc_type,
        publisher: form.publisher.trim(),
        doi: form.doi.trim(),
        arxiv_id: form.arxiv_id.trim(),
        isbn: form.isbn.trim(),
        aliases: splitList(form.aliases),
        summary: form.summary.trim(),
      });
      onSaved(res.document);
      setForm(toForm(res.document.meta, res.document.title));
      setEditing(false);
      setMessage(
        "Đã lưu. Nhận diện tài liệu trong câu hỏi dùng ngay metadata mới; bấm \"Xử lý lại\" để cập nhật cả ngữ cảnh tiêu đề/tác giả trong embedding."
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!editing) {
    return (
      <div className="meta-view">
        <Row label="Tiêu đề" value={meta?.title || doc.title} />
        <Row label="Tên ngắn" value={meta?.short_title ?? ""} />
        <Row label="Tác giả" value={(meta?.authors ?? []).join(", ")} />
        <Row label="Năm" value={meta?.year ? String(meta.year) : ""} />
        <Row label="Loại" value={meta?.doc_type ?? ""} />
        <Row label="Nơi xuất bản" value={meta?.publisher ?? ""} />
        <Row label="DOI" value={meta?.doi ?? ""} />
        <Row label="arXiv" value={meta?.arxiv_id ?? ""} />
        <Row label="ISBN" value={meta?.isbn ?? ""} />
        <Row label="Tên gọi khác" value={(meta?.aliases ?? []).join(", ")} />
        <Row label="Tóm tắt" value={meta?.summary ?? ""} />
        <Row label="Nguồn metadata" value={meta?.source === "manual" ? "sửa tay" : "tự động"} />
        <p className="hint">
          Khi câu hỏi nhắc tới tiêu đề, tên gọi khác, tác giả ("Vaswani et al."), DOI/arXiv/ISBN của tài liệu này, hệ
          thống chỉ tìm trong tài liệu này.
        </p>
        {message && <div className="notice">{message}</div>}
        {isAdmin && (
          <button className="primary-button" onClick={() => setEditing(true)}>
            Sửa metadata
          </button>
        )}
      </div>
    );
  }

  return (
    <form className="meta-form" onSubmit={onSubmit}>
      <label>
        Tiêu đề
        <input value={form.title} onChange={(e) => set("title", e.target.value)} required maxLength={500} />
      </label>
      <label>
        Tên ngắn
        <input value={form.short_title} onChange={(e) => set("short_title", e.target.value)} maxLength={200} />
      </label>
      <label>
        Tác giả (ngăn cách bằng dấu ;)
        <input value={form.authors} onChange={(e) => set("authors", e.target.value)} />
      </label>
      <div className="meta-grid">
        <label>
          Năm
          <input value={form.year} onChange={(e) => set("year", e.target.value)} inputMode="numeric" />
        </label>
        <label>
          Loại
          <select value={form.doc_type} onChange={(e) => set("doc_type", e.target.value as DocType)}>
            {DOC_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label>
        Nơi xuất bản
        <input value={form.publisher} onChange={(e) => set("publisher", e.target.value)} maxLength={300} />
      </label>
      <div className="meta-grid">
        <label>
          DOI
          <input value={form.doi} onChange={(e) => set("doi", e.target.value)} />
        </label>
        <label>
          arXiv
          <input value={form.arxiv_id} onChange={(e) => set("arxiv_id", e.target.value)} />
        </label>
        <label>
          ISBN
          <input value={form.isbn} onChange={(e) => set("isbn", e.target.value)} />
        </label>
      </div>
      <label>
        Tên gọi khác (ngăn cách bằng dấu ;)
        <input value={form.aliases} onChange={(e) => set("aliases", e.target.value)} />
      </label>
      <label>
        Tóm tắt
        <textarea value={form.summary} onChange={(e) => set("summary", e.target.value)} rows={3} maxLength={1000} />
      </label>
      {error && <div className="error">{error}</div>}
      <div className="meta-actions">
        <button type="submit" className="primary-button" disabled={busy}>
          {busy ? "Đang lưu…" : "Lưu"}
        </button>
        <button
          type="button"
          onClick={() => {
            setEditing(false);
            setForm(toForm(meta, doc.title));
            setError("");
          }}
        >
          Hủy
        </button>
      </div>
    </form>
  );
}
