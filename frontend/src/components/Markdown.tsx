import { useEffect, useState } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { fetchFigure } from "../api.ts";

interface MarkdownProps {
  children?: string | null;
}

const FIGURE_RE = /^figure:\/\/([0-9a-f-]{36})\/(figure_\d{1,4}\.png)$/i;
const DISPLAY_MATH_LINE_RE = /^[ \t]*\$\$[ \t]*(\S.*?)[ \t]*\$\$[ \t]*$/gm;

function displayMathBlocks(md: string): string {
  return md.replace(DISPLAY_MATH_LINE_RE, (_line, tex: string) => `$$\n${tex}\n$$`);
}

function urlTransform(url: string): string {
  return FIGURE_RE.test(url) ? url : defaultUrlTransform(url);
}

function FigureImage({ src, alt }: { src: string; alt: string }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const match = FIGURE_RE.exec(src);
    if (!match) return;
    let url: string | null = null;
    let cancelled = false;
    fetchFigure(match[1]!, match[2]!)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setObjectUrl(url);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [src]);

  if (failed) return <span className="figure-missing">[Không tải được hình: {alt}]</span>;
  if (!objectUrl) return <span className="figure-loading">Đang tải hình…</span>;
  return <img className="figure" src={objectUrl} alt={alt} loading="lazy" />;
}

const components: Components = {
  img({ src, alt }) {
    const source = typeof src === "string" ? src : "";
    if (FIGURE_RE.test(source)) return <FigureImage src={source} alt={alt ?? ""} />;
    return <span className="figure-missing">[Hình ngoài: {alt}]</span>;
  },
};

export default function Markdown({ children }: MarkdownProps) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        urlTransform={urlTransform}
        components={components}
      >
        {displayMathBlocks(children ?? "")}
      </ReactMarkdown>
    </div>
  );
}
