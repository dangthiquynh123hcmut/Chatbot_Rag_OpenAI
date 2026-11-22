import type {
  DocumentInfo,
  HealthInfo,
  QueryResult,
  SectionDetail,
  SectionNode,
  UploadResult,
} from "./types.ts";

const BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function readError(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const body: unknown = JSON.parse(text);
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      return typeof detail === "string" ? detail : JSON.stringify(detail);
    }
  } catch {
    return text || res.statusText;
  }
  return text || res.statusText;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) throw new ApiError(res.status, await readError(res));
  return (await res.json()) as T;
}

async function requestText(path: string, init?: RequestInit): Promise<string> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) throw new ApiError(res.status, await readError(res));
  return res.text();
}

export const api = {
  health: () => requestJson<HealthInfo>("/health"),
  listDocuments: () => requestJson<{ documents: DocumentInfo[] }>("/documents"),
  uploadDocuments: (files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f);
    return requestJson<UploadResult>("/documents", { method: "POST", body: form });
  },
  deleteDocument: (id: string) => requestJson<{ deleted: string }>(`/documents/${id}`, { method: "DELETE" }),
  reindexDocument: (id: string) =>
    requestJson<{ id: string; status: string }>(`/documents/${id}/reindex`, { method: "POST" }),
  getTree: (id: string) => requestJson<{ sections: SectionNode[] }>(`/documents/${id}/tree`),
  getMarkdown: (id: string) => requestText(`/documents/${id}/markdown`),
  getSection: (id: string) => requestJson<SectionDetail>(`/sections/${id}`),
  query: (query: string, documentIds: string[], topK?: number) =>
    requestJson<QueryResult>("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        document_ids: documentIds.length ? documentIds : null,
        top_k: topK ?? null,
      }),
    }),
};

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
