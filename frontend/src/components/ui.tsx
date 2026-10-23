import type { ReactNode } from 'react';
import { ApiError } from '../api/client';
import type { Citation, Document } from '../api/types';

export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    home: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),
    docs: (
      <>
        <path d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8M8 16h6" />
      </>
    ),
    chat: <path d="M4 4h16v13H9l-5 4V4zm4 5h8m-8 4h5" />,
    agent: (
      <>
        <circle cx="5" cy="5" r="2" />
        <circle cx="19" cy="12" r="2" />
        <circle cx="5" cy="19" r="2" />
        <path d="M7 5h5v14H7m5-7h5" />
      </>
    ),
    tasks: <path d="M9 5h12M9 12h12M9 19h12M2 5l2 2 3-4M2 12l2 2 3-4M2 19l2 2 3-4" />,
    status: (
      <>
        <path d="M2 12h4l3-8 6 16 3-8h4" />
      </>
    ),
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    plus: <path d="M12 4v16M4 12h16" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.docs}
    </svg>
  );
}
export function Badge({ children, tone = '' }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="notice error" role="alert">
      <strong>操作未完成</strong>
      <p>{error instanceof Error ? error.message : '服务暂时不可用。'}</p>
      {error instanceof ApiError && (
        <small>
          错误：{error.code}
          {error.requestId && ` · 请求 ID ${error.requestId}`}
        </small>
      )}
    </div>
  );
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name="docs" size={30} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading({ text = '正在读取真实数据…' }: { text?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {text}
    </div>
  );
}
export function date(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(
    new Date(value),
  );
}
export function Sources({
  citations,
  documents = [],
}: {
  citations: Citation[];
  documents?: Document[];
}) {
  return (
    <div className="sources">
      {citations.map((source, index) => (
        <details key={`${source.segment_id}-${index}`}>
          <summary>
            <span className="evidence-id">{source.evidence_id}</span>
            <strong>
              {documents.find((doc) => doc.id === source.document_id)?.title ?? source.document_key}
            </strong>
            <span>
              v{source.version_number} · 第 {source.line_start}–{source.line_end} 行
            </span>
          </summary>
          <blockquote>{source.excerpt}</blockquote>
          <dl className="source-meta">
            <dt>来源</dt>
            <dd>
              Chunk {source.chunk_index} / Segment {source.segment_index}
            </dd>
            <dt>字符范围</dt>
            <dd>
              {source.char_start}–{source.char_end}（左闭右开）
            </dd>
            <dt>版本 ID</dt>
            <dd>{source.version_id}</dd>
            <dt>Segment ID</dt>
            <dd>{source.segment_id}</dd>
          </dl>
        </details>
      ))}
    </div>
  );
}
export function DocumentSelect({
  documents,
  value,
  onChange,
}: {
  documents: Document[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      文档范围
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">我的全部知识库</option>
        {documents.map((doc) => (
          <option key={doc.id} value={doc.document_key}>
            {doc.title} · {doc.document_key}
          </option>
        ))}
      </select>
    </label>
  );
}
