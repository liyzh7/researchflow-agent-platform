export interface User {
  id: string;
  username: string;
  is_active: boolean;
  is_admin: boolean;
}
export interface Session {
  access_token: string;
  token_type: string;
  expires_at: string;
}
export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}
export interface Version {
  id: string;
  version_number: number;
  sha256: string;
  original_filename: string;
  content_type: string;
  file_size: number;
  created_at: string;
  chunk_count: number;
}
export interface Document {
  id: string;
  document_key: string;
  title: string;
  updated_at: string;
  current_version: Version | null;
}
export interface Chunk {
  id: string;
  chunk_index: number;
  content: string;
  char_start: number;
  char_end: number;
  line_start: number;
  line_end: number;
}
export interface IndexState {
  status: 'not_indexed' | 'running' | 'completed' | 'failed';
  segment_count: number;
  error_code: string | null;
  updated_at: string | null;
}
export interface Citation {
  evidence_id: string;
  document_id: string;
  document_key: string;
  version_id: string;
  version_number: number;
  chunk_id: string;
  chunk_index: number;
  segment_id: string;
  segment_index: number;
  char_start: number;
  char_end: number;
  line_start: number;
  line_end: number;
  excerpt: string;
}
export interface Answer {
  answer: string;
  status: 'answered' | 'refused';
  is_mock: boolean;
  provider_name: string | null;
  provider_model: string | null;
  citations: Citation[];
  evidence_status: string;
  evidence_reasons: string[];
  input_tokens: number | null;
  output_tokens: number | null;
  timing: { total_ms: number };
}
export interface DraftItem {
  title: string;
  description: string;
  evidence_id: string;
  source_quote: string;
  basis: 'documented' | 'suggestion';
}
export interface Draft {
  tasks: DraftItem[];
}
export type RunStatus = 'RUNNING' | 'AWAITING_APPROVAL' | 'COMPLETED' | 'REJECTED' | 'FAILED';
export interface RunSummary {
  run_id: string;
  status: RunStatus;
  query: string;
  updated_at: string;
  expires_at: string;
}
export interface Run {
  run_id: string;
  status: RunStatus;
  error_code: string | null;
  expires_at: string;
  checkpoint_status: string;
  next_nodes: string[];
  workflow_token?: string;
  result: Record<string, unknown> | null;
  proposal: null | {
    id: string;
    version: number;
    content_digest: string;
    status: string;
    content: Draft;
    evidence: Citation[];
  };
  approval: null | { id: string; decision: string; execution_status: string };
}
export interface Task {
  id: string;
  title: string;
  description: string;
  status: 'TODO' | 'IN_PROGRESS' | 'DONE';
  workflow_id: string;
  approval_id: string;
  created_at: string;
  source_evidence: Citation;
}
export interface Info {
  project_name: string;
  version: string;
  environment: string;
  provider_mode: 'mock' | 'openai_compatible';
  rag_enabled: boolean;
  agent_enabled: boolean;
  upload_max_bytes: number;
}
export interface Ready {
  status: 'ready' | 'not_ready';
  database: string;
  pgvector_version: string | null;
}
