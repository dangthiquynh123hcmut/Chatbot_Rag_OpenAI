import { useState } from "react";
import type { QueryResult, Source } from "../types.ts";
import Breadcrumb from "./Breadcrumb.tsx";
import Markdown from "./Markdown.tsx";

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

export default function ResultView({ result }: { result: QueryResult }) {
  const [showSources, setShowSources] = useState(false);
  const primary = result.primary;
  const refs = result.references_detected ?? {};
  const detected = result.documents_detected ?? [];

  return (
    <section className="result">
      {result.scope === "detected" && detected.length > 0 && (
        <div className="refs">
          Tự giới hạn vào tài liệu:{" "}
          {detected.map((d) => (
            <code key={d.id} title={`Khớp "${d.matched}" (${d.match_kind})`}>
              {d.title}
            </code>
          ))}
        </div>
      )}
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
          <button className="link-button" onClick={() => setShowSources(!showSources)}>
            {showSources ? "Ẩn" : "Xem"} {result.sources.length} nguồn sau rerank
          </button>
          {showSources && result.sources.map((s) => <SourceCard key={s.section_id} source={s} />)}
        </div>
      )}
    </section>
  );
}
