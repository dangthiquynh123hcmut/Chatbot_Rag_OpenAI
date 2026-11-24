export type DocumentStatus = "processing" | "ready" | "error";

export interface DocumentInfo {
  id: string;
  filename: string;
  title: string;
  status: DocumentStatus;
  error: string | null;
  num_sections: number;
  num_chunks: number;
  created_at: string;
  meta?: DocumentMeta;
}

export type DocType = "paper" | "book" | "report" | "manual" | "other";

export interface DocumentMeta {
  title: string;
  short_title: string;
  authors: string[];
  year: number | null;
  doc_type: DocType;
  publisher: string;
  isbn: string;
  aliases: string[];
  summary: string;
  source: "auto" | "manual";
}

export interface DetectedDocument {
  id: string;
  title: string;
  matched: string;
  match_kind: string;
}

export type SectionKind =
  | "part"
  | "chapter"
  | "section"
  | "subsection"
  | "article"
  | "clause"
  | "point"
  | "roman"
  | "num"
  | "md";

export type Refs = Partial<Record<SectionKind, string>>;

export interface SectionNode {
  id: string;
  parent_id: string | null;
  depth: number;
  kind: SectionKind;
  label: string | null;
  title: string | null;
  heading: string | null;
  refs: Refs;
}

export interface SectionDetail {
  id: string;
  document_id: string;
  document_title: string;
  kind: SectionKind;
  label: string | null;
  title: string | null;
  heading: string | null;
  path: string[];
  refs: Refs;
  full_md: string | null;
}

export type MatchType = "reference" | "semantic";

export interface Source {
  rank: number;
  section_id: string;
  document_id: string;
  document_title: string;
  path: string[];
  heading: string;
  content: string;
  matched_chunk: string | null;
  match_type: MatchType;
  vector_score: number | null;
  keyword_score: number | null;
  fused_score: number;
  rerank_score: number | null;
}

export interface PrimaryResult {
  section_id: string;
  document_id: string;
  document_title: string;
  path: string[];
  heading: string;
  content: string;
  parent_path: string[];
  quote: string;
  quote_verified: boolean;
  match_type: MatchType;
}

export interface QueryResult {
  query: string;
  references_detected: Record<string, string>;
  documents_detected?: DetectedDocument[];
  scope?: "all" | "selected" | "detected";
  found: boolean;
  answer: string;
  primary: PrimaryResult | null;
  sources: Source[];
}

export interface UploadResult {
  documents: Array<Pick<DocumentInfo, "id" | "filename" | "status">>;
}

export interface HealthInfo {
  status: string;
  provider: "openai" | "bedrock";
  llm_model: string;
  embedding_model: string;
  embedding_dim: number;
  reranker: string;
  aws_region: string | null;
}

export type UserRole = "user" | "admin";

export interface User {
  id: string;
  username: string;
  display_name: string | null;
  role: UserRole;
  created_at: string;
  last_login_at: string | null;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: User;
}

export interface AuthConfig {
  allow_registration: boolean;
  min_password_length: number;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
}

export interface AssistantError {
  error: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  result: QueryResult | AssistantError | null;
  document_ids: string[] | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

export interface SendMessageResponse {
  conversation: Conversation;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}
