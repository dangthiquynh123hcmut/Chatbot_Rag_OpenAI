import { useEffect, useState } from "react";
import { api, errorMessage } from "../api.ts";
import type { DocumentInfo, SectionDetail, SectionNode } from "../types.ts";
import Breadcrumb from "./Breadcrumb.tsx";
import Markdown from "./Markdown.tsx";
import MetadataPanel from "./MetadataPanel.tsx";

type Tab = "tree" | "md" | "info";

interface DocumentViewerProps {
  doc: DocumentInfo;
  isAdmin: boolean;
  onClose: () => void;
  onUpdated: (doc: DocumentInfo) => void;
}

export default function DocumentViewer({ doc, isAdmin, onClose, onUpdated }: DocumentViewerProps) {
  const [tab, setTab] = useState<Tab>("tree");
  const [sections, setSections] = useState<SectionNode[]>([]);
  const [markdown, setMarkdown] = useState("");
  const [active, setActive] = useState<SectionDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    setActive(null);
    Promise.all([api.getTree(doc.id), api.getMarkdown(doc.id)])
      .then(([tree, md]) => {
        if (cancelled) return;
        setSections(tree.sections);
        setMarkdown(md);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [doc.id]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function openSection(id: string) {
    try {
      setActive(await api.getSection(id));
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>{doc.title}</h2>
          <div className="tabs">
            <button className={tab === "tree" ? "active" : ""} onClick={() => setTab("tree")}>
              Cấu trúc phân tầng
            </button>
            <button className={tab === "md" ? "active" : ""} onClick={() => setTab("md")}>
              Markdown
            </button>
            <button className={tab === "info" ? "active" : ""} onClick={() => setTab("info")}>
              Thông tin
            </button>
          </div>
          <button className="close" onClick={onClose} aria-label="Đóng">
            ✕
          </button>
        </header>
        {error && <div className="error">{error}</div>}
        {tab === "info" ? (
          <div className="md-view">
            <MetadataPanel doc={doc} isAdmin={isAdmin} onSaved={onUpdated} />
          </div>
        ) : tab === "tree" ? (
          <div className="viewer">
            <ul className="tree">
              {sections.map((s) => (
                <li
                  key={s.id}
                  style={{ paddingLeft: `${(s.depth - 1) * 14 + 6}px` }}
                  className={`${active?.id === s.id ? "active" : ""} kind-${s.kind}`}
                  onClick={() => void openSection(s.id)}
                >
                  {s.name}
                </li>
              ))}
            </ul>
            <div className="section-view">
              {active ? (
                <>
                  <Breadcrumb path={active.path} />
                  <Markdown>{active.full_md}</Markdown>
                </>
              ) : (
                <p className="hint">Chọn một mục bên trái để xem nội dung.</p>
              )}
            </div>
          </div>
        ) : (
          <div className="md-view">
            <Markdown>{markdown}</Markdown>
          </div>
        )}
      </div>
    </div>
  );
}
