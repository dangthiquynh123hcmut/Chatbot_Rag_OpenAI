import { useState, type FormEvent, type KeyboardEvent } from "react";
import { api, errorMessage } from "../api.ts";
import type { QueryResult, Source } from "../types.ts";
import Breadcrumb from "./Breadcrumb.tsx";
import Markdown from "./Markdown.tsx";

const EXAMPLES: readonly string[] = [
  "Điều 3 quy định những gì?",
  "Điểm b khoản 2 Điều 3",
  "Phạm vi điều chỉnh của văn bản là gì?",
];

function fmt(v: number | null | undefined, digits = 3): string {
  return typeof v === "number" ? v.toFixed(digits) : "—";
}

function SourceCard({ source }: { source: Source }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="source">
      <button className="source-head" onClick={() => setOpen(!open)}>
        <span className="rank">#{source.rank}</span>
        <Breadcrumb documentTitle={source.document_title} path={source.path} />
        <span className={`tag ${source.match_type}`}>
          {source.match_type === "reference" ? "Khớp số hiệu" : "Ngữ nghĩa"}
        </span>
      </button>
      <div className="scores">
        rerank {fmt(source.rerank_score, 1)} · vector {fmt(source.vector_score)} · keyword{" "}
        {fmt(source.keyword_score)} · RRF {fmt(source.fused_score, 4)}
      </div>
      {open && <Markdown>{source.content}</Markdown>}
    </div>
  );
}

interface QueryPanelProps {
  selected: string[];
}

export default function QueryPanel({ selected }: QueryPanelProps) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);

  async function ask(text: string) {
    const q = text.trim();
    if (q.length < 2) return;
    setQuery(q);
    setLoading(true);
    setError("");
    try {
      setResult(await api.query(q, selected));
    } catch (err) {
      setError(errorMessage(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    void ask(query);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void ask(query);
    }
  }

  const primary = result?.primary ?? null;
  const refs = result?.references_detected ?? {};

  return (
    <main className="panel query">
      <form className="ask" onSubmit={onSubmit}>
        <textarea
          value={query}
          placeholder="Nhập câu hỏi, ví dụ: Khoản 2 Điều 3 quy định gì?"
          rows={3}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" disabled={loading}>
          {loading ? "Đang tìm…" : "Tra cứu"}
        </button>
      </form>
      <div className="examples">
        {EXAMPLES.map((ex) => (
          <button key={ex} onClick={() => void ask(ex)} disabled={loading}>
            {ex}
          </button>
        ))}
      </div>

      {error && <div className="error">{error}</div>}

      {result && (
        <section className="result">
          {Object.keys(refs).length > 0 && (
            <div className="refs">
              Nhận diện số hiệu:{" "}
              {Object.entries(refs).map(([k, v]) => (
                <code key={k}>
                  {k}={v}
                </code>
              ))}
            </div>
          )}

          <div className={`answer ${result.found ? "found" : "not-found"}`}>
            <h3>Trả lời</h3>
            <p>{result.answer}</p>
          </div>

          {primary && (
            <div className="primary">
              <h3>Mục chính xác trong tài liệu</h3>
              <Breadcrumb documentTitle={primary.document_title} path={primary.path} />
              {primary.quote && (
                <blockquote className={primary.quote_verified ? "verified" : "unverified"}>
                  <div className="quote-label">
                    Trích nguyên văn{" "}
                    {primary.quote_verified ? "✓ đã đối chiếu với tài liệu" : "⚠ chưa khớp nguyên văn"}
                  </div>
                  {primary.quote}
                </blockquote>
              )}
              <div className="verbatim">
                <div className="verbatim-label">Toàn văn mục</div>
                <Markdown>{primary.content}</Markdown>
              </div>
            </div>
          )}

          {result.sources.length > 0 && (
            <div className="sources">
              <h3>Các nguồn sau rerank ({result.sources.length})</h3>
              {result.sources.map((s) => (
                <SourceCard key={s.section_id} source={s} />
              ))}
            </div>
          )}
        </section>
      )}
    </main>
  );
}
