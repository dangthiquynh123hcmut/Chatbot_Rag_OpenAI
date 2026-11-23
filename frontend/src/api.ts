import type {
  AuthConfig,
  AuthResponse,
  Conversation,
  ConversationDetail,
  DocumentInfo,
  DocumentMeta,
  HealthInfo,
  SectionDetail,
  SectionNode,
  SendMessageResponse,
  UploadResult,
  User,
} from "./types.ts";

const BASE: string = import.meta.env.VITE_API_BASE ?? "/api";
const TOKEN_KEY = "rag.access_token";

class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    return;
  }
}

async function readError(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const body: unknown = JSON.parse(text);
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        return detail
          .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
          .join("; ");
      }
      return JSON.stringify(detail);
    }
  } catch {
    return text || res.statusText;
  }
  return text || res.statusText;
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const message = await readError(res);
    if (res.status === 401 && token && unauthorizedHandler) unauthorizedHandler();
    throw new ApiError(res.status, message);
  }
  return res;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  return (await (await send(path, init)).json()) as T;
}

async function requestText(path: string, init?: RequestInit): Promise<string> {
  return (await send(path, init)).text();
}

function jsonBody(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  health: () => requestJson<HealthInfo>("/health"),
  authConfig: () => requestJson<AuthConfig>("/auth/config"),
  login: (username: string, password: string) =>
    requestJson<AuthResponse>("/auth/login", jsonBody("POST", { username, password })),
  register: (username: string, password: string, displayName: string) =>
    requestJson<AuthResponse>(
      "/auth/register",
      jsonBody("POST", { username, password, display_name: displayName || null })
    ),
  me: () => requestJson<User>("/auth/me"),
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
  updateMetadata: (id: string, meta: Partial<DocumentMeta>) =>
    requestJson<{ document: DocumentInfo; reindex_recommended: boolean }>(
      `/documents/${id}/metadata`,
      jsonBody("PATCH", meta)
    ),
  listConversations: () => requestJson<{ conversations: Conversation[] }>("/conversations"),
  createConversation: () => requestJson<Conversation>("/conversations", jsonBody("POST", {})),
  getConversation: (id: string) => requestJson<ConversationDetail>(`/conversations/${id}`),
  renameConversation: (id: string, title: string) =>
    requestJson<Conversation>(`/conversations/${id}`, jsonBody("PATCH", { title })),
  deleteConversation: (id: string) =>
    requestJson<{ deleted: string }>(`/conversations/${id}`, { method: "DELETE" }),
  sendMessage: (conversationId: string, query: string, documentIds: string[]) =>
    requestJson<SendMessageResponse>(
      `/conversations/${conversationId}/messages`,
      jsonBody("POST", { query, document_ids: documentIds.length ? documentIds : null })
    ),
};

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
