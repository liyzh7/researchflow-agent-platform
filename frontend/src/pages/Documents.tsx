import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import type { ApiClient } from '../api/client';
import type { Chunk, Document, IndexState, Info, Version } from '../api/types';
import { Badge, date, Empty, ErrorBox, Loading } from '../components/ui';
import { PageTitle } from '../app/App';

const indexLabel: Record<IndexState['status'], string> = {
  not_indexed: '未建立索引',
  running: '正在执行',
  completed: '已完成',
  failed: '失败',
};
export function DocumentsPage({
  api,
  documents,
  info,
  refresh,
}: {
  api: ApiClient;
  documents: Document[];
  info?: Info;
  refresh: () => void;
}) {
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Document>();
  const [versions, setVersions] = useState<Version[]>([]);
  const [version, setVersion] = useState('');
  const [chunks, setChunks] = useState<Chunk[]>();
  const [states, setStates] = useState<Record<string, IndexState>>({});
  const [indexing, setIndexing] = useState(false);
  useEffect(() => {
    const latest = documents.find((doc) => doc.id === selected?.id);
    if (latest && latest.current_version?.id !== selected?.current_version?.id) setSelected(latest);
  }, [documents, selected]);
  useEffect(() => {
    let current = true;
    void Promise.all(
      documents.map(async (doc) => {
        if (!doc.current_version) return;
        try {
          const status = await api.request<IndexState>(
            `/api/v1/documents/${doc.id}/versions/${doc.current_version.id}/index`,
          );
          if (current) setStates((old) => ({ ...old, [doc.current_version!.id]: status }));
        } catch (caught) {
          if (current) setError(caught);
        }
      }),
    );
    return () => {
      current = false;
    };
  }, [api, documents]);
  useEffect(() => {
    if (!selected) return;
    let current = true;
    setVersions([]);
    setVersion('');
    setChunks(undefined);
    void api
      .request<Version[]>(`/api/v1/documents/${selected.id}/versions`)
      .then((value) => {
        if (current) {
          setVersions(value);
          setVersion(selected.current_version?.id ?? '');
        }
      })
      .catch((caught) => {
        if (current) setError(caught);
      });
    return () => {
      current = false;
    };
  }, [api, selected]);
  useEffect(() => {
    if (!selected || !version) return;
    let current = true;
    setChunks(undefined);
    void Promise.all([
      api.request<Chunk[]>(`/api/v1/documents/${selected.id}/versions/${version}/chunks`),
      api.request<IndexState>(`/api/v1/documents/${selected.id}/versions/${version}/index`),
    ])
      .then(([data, status]) => {
        if (current) {
          setChunks(data);
          setStates((old) => ({ ...old, [version]: status }));
        }
      })
      .catch((caught) => {
        if (current) setError(caught);
      });
    return () => {
      current = false;
    };
  }, [api, selected, version]);
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = data.get('file');
    setError(undefined);
    setMessage('');
    if (
      !(file instanceof File) ||
      !/\.(txt|md)$/i.test(file.name) ||
      file.size === 0 ||
      file.size > (info?.upload_max_bytes ?? 2097152)
    ) {
      setError(new Error('请选择非空 TXT / Markdown 文件，文件不可超过配置大小；暂不支持 PDF。'));
      return;
    }
    setBusy(true);
    try {
      const result = await api.request<{
        is_duplicate: boolean;
        version_number: number;
        chunk_count: number;
      }>('/api/v1/documents', { method: 'POST', body: data });
      setMessage(
        `${result.is_duplicate ? '检测到重复上传，复用已有版本' : '上传完成'}：v${result.version_number}，${result.chunk_count} 个 Chunk。尚需检查/建立索引。`,
      );
      form.reset();
      refresh();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  async function index() {
    if (!selected || !version) return;
    setIndexing(true);
    setError(undefined);
    try {
      await api.post(`/api/v1/documents/${selected.id}/versions/${version}/index`);
      const state = await api.request<IndexState>(
        `/api/v1/documents/${selected.id}/versions/${version}/index`,
      );
      setStates((old) => ({ ...old, [version]: state }));
    } catch (caught) {
      setError(caught);
    } finally {
      setIndexing(false);
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="RESEARCH LIBRARY"
        title="你的知识，有序可溯"
        subtitle="上传与版本管理独立于 Embedding。原文、分块和索引状态均来自后端。"
      />
      <ErrorBox error={error} />
      {message && (
        <div className="notice success" role="status">
          {message}
        </div>
      )}
      <div className="library-layout">
        <section>
          <div className="section-heading">
            <h2>
              科研文档 <span className="muted">{documents.length}</span>
            </h2>
            <button onClick={refresh}>刷新列表</button>
          </div>
          <div className="panel table-wrap">
            <table>
              <thead>
                <tr>
                  <th>文档 / Key</th>
                  <th>当前版本</th>
                  <th>索引状态</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.id}>
                    <td>
                      <strong>{doc.title}</strong>
                      <small>{doc.document_key}</small>
                    </td>
                    <td>v{doc.current_version?.version_number ?? '—'}</td>
                    <td>
                      <Badge
                        tone={
                          states[doc.current_version?.id ?? '']?.status === 'completed'
                            ? 'green'
                            : ''
                        }
                      >
                        {states[doc.current_version?.id ?? '']
                          ? indexLabel[states[doc.current_version?.id ?? '']!.status]
                          : '尚未读取'}
                      </Badge>
                    </td>
                    <td>{date(doc.updated_at)}</td>
                    <td>
                      <button
                        onClick={() => {
                          setSelected(doc);
                          setError(undefined);
                        }}
                      >
                        查看详情
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!documents.length && <Empty title="知识库还没有文档">从右侧导入一份研究资料。</Empty>}
          </div>
          <p className="muted">当前展示最近最多 100 份本人文档。</p>
        </section>
        <section className="panel upload-panel">
          <Badge>IMPORT DOCUMENT</Badge>
          <h2>导入研究资料</h2>
          <p className="muted">相同 Key 创建历史版本；相同内容由服务端判断是否重复。</p>
          <form onSubmit={upload}>
            <label>
              文档标题
              <input name="title" required maxLength={300} placeholder="例如：索引卡分类说明" />
            </label>
            <label>
              文档 Key
              <input
                name="document_key"
                required
                maxLength={128}
                pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*"
                placeholder="例如：sensor-protocol"
              />
            </label>
            <label className="file-drop">
              TXT / Markdown 文件
              <input name="file" type="file" required accept=".txt,.md" />
              <small>
                上限 {((info?.upload_max_bytes ?? 2097152) / 1048576).toFixed(0)} MiB · 暂不支持 PDF
                / OCR
              </small>
            </label>
            <button className="primary wide" disabled={busy}>
              {busy ? '正在上传并解析…' : '上传到我的知识库'}
            </button>
          </form>
        </section>
      </div>
      {selected && (
        <section className="panel detail-panel">
          <div className="section-heading">
            <div>
              <Badge>DOCUMENT DETAIL</Badge>
              <h2>{selected.title}</h2>
            </div>
            <button onClick={() => setSelected(undefined)}>关闭详情</button>
          </div>
          <div className="inline-form">
            <label>
              文档版本
              <select value={version} onChange={(e) => setVersion(e.target.value)}>
                {versions.map((value) => (
                  <option key={value.id} value={value.id}>
                    v{value.version_number}{' '}
                    {value.id === selected.current_version?.id ? '· 当前版本' : '· 历史版本'} ·{' '}
                    {value.chunk_count} Chunks
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={!version || indexing}
              onClick={() => {
                void index();
              }}
            >
              {indexing ? '正在索引，可能需要冷启动…' : '建立 / 检查索引'}
            </button>
            <Badge>{states[version] ? indexLabel[states[version]!.status] : '尚未读取'}</Badge>
          </div>
          {indexing && (
            <Loading text="正在等待后端索引完成，不提供虚假进度；超时后请重新查看状态。" />
          )}
          <small>
            SHA-256 {versions.find((item) => item.id === version)?.sha256 ?? '正在读取'}
          </small>
          {chunks ? (
            <div className="chunks">
              {chunks.map((chunk) => (
                <details key={chunk.id}>
                  <summary>
                    Chunk {chunk.chunk_index} · 第 {chunk.line_start}–{chunk.line_end} 行 · 字符{' '}
                    {chunk.char_start}–{chunk.char_end}
                  </summary>
                  <pre>{chunk.content}</pre>
                </details>
              ))}
            </div>
          ) : (
            <Loading />
          )}
        </section>
      )}
    </>
  );
}
