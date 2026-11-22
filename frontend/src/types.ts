export type DocumentStatus = "processing" | "ready" | "error";

export interface DocumentInfo {
  id: string;
  filename: string;
  file_type: string;
  title: string;
  status: DocumentStatus;
  error: string | null;
  num_sections: number;
  num_chunks: number;
  created_at: string;
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
  number: string | null;
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
