import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError } from '../api/client';
import type { ApiClient } from '../api/client';
import type { Document, Draft, Run, RunStatus, RunSummary } from '../api/types';
import { Badge, date, DocumentSelect, Empty, ErrorBox, Loading, Sources } from '../components/ui';
import { PageTitle } from '../app/App';

type Decision = 'APPROVE' | 'REJECT' | 'EDIT_AND_REVIEW';
const labels: Record<RunStatus, string> = {
  RUNNING: '运行中',
  AWAITING_APPROVAL: '等待人工审批',
  COMPLETED: '已完成',
  REJECTED: '已拒绝',
  FAILED: '执行失败',
};
export function AgentPage({
  api,
  documents,
  summaries,
  enabled,
  mockMode,
  refresh,
}: {
  api: ApiClient;
  documents: Document[];
  summaries: RunSummary[];
  enabled: boolean;
  mockMode: boolean;
  refresh: () => void;
}) {
  const initial = location.hash.split('/')[1] ?? '';
  const [runId, setRunId] = useState(/^[0-9a-f-]{36}$/.test(initial) ? initial : '');
  const [run, setRun] = useState<Run>();
  const capability = useRef<string | null>(null);
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [uncertain, setUncertain] = useState(false);
  const [polling, setPolling] = useState(true);
  const [confirm, setConfirm] = useState<Decision>();
  const [edited, setEdited] = useState<Draft>();
  const [recoveryRevision, setRecoveryRevision] = useState(0);
  const idempotencyKey = useRef(crypto.randomUUID());
  useEffect(() => {
    const change = () => {
      const id = location.hash.split('/')[1] ?? '';
      if (/^[0-9a-f-]{36}$/.test(id)) setRunId(id);
    };
    window.addEventListener('hashchange', change);
    return () => window.removeEventListener('hashchange', change);
  }, []);
  useEffect(() => {
    if (!runId || !enabled) return;
    let current = true;
    capability.current = null;
    setRun(undefined);
    setError(undefined);
    void (async () => {
      try {
        const access = await api.post<{ workflow_token: string }>(
          `/api/v1/agent/runs/${runId}/access`,
        );
        if (!current) return;
        capability.current = access.workflow_token;
        const value = await api.request<Run>(`/api/v1/agent/runs/${runId}`, {
          headers: { 'X-Workflow-Token': access.workflow_token },
        });
        if (current) {
          setRun(value);
          setUncertain(false);
        }
      } catch (caught) {
        if (current) setError(caught);
      }
    })();
    return () => {
      current = false;
    };
  }, [api, runId, enabled, recoveryRevision]);
  useEffect(() => {
    if (run?.status !== 'RUNNING' || !polling || !capability.current) return;
    let active = true;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const value = await api.request<Run>(`/api/v1/agent/runs/${run.run_id}`, {
          headers: { 'X-Workflow-Token': capability.current! },
        });
        if (!active) return;
        setRun(value);
        attempts++;
        if (value.status === 'RUNNING' && attempts < 60)
          timer = setTimeout(() => {
            void tick();
          }, 3000);
        else if (attempts >= 60) setPolling(false);
      } catch (caught) {
        if (active) {
          setError(caught);
          setPolling(false);
        }
      }
    };
    timer = setTimeout(() => {
      void tick();
    }, 3000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [api, run?.status, run?.run_id, polling]);
  async function inspect() {
    if (!runId || !capability.current) {
      setError(new Error('审批凭据缺失，请从本人工作流列表重新打开。'));
      return;
    }
    setError(undefined);
    try {
      setRun(
        await api.request<Run>(`/api/v1/agent/runs/${runId}`, {
          headers: { 'X-Workflow-Token': capability.current },
        }),
      );
    } catch (caught) {
      setError(caught);
    }
  }
  async function create(event: FormEvent) {
    event.preventDefault();
    if (busy || uncertain) return;
    setBusy(true);
    setError(undefined);
    try {
      const value = await api.post<Run>(
        '/api/v1/agent/runs',
        { query, document_key: scope || undefined, intent: 'tasks' },
        { 'Idempotency-Key': idempotencyKey.current },
      );
      capability.current = value.workflow_token ?? null;
      setRun(value);
      setRunId(value.run_id);
      location.hash = `agent/${value.run_id}`;
      idempotencyKey.current = crypto.randomUUID();
      setQuery('');
    } catch (caught) {
      setError(caught);
      if (!(caught instanceof ApiError) || caught.status === 0 || caught.status >= 500)
        setUncertain(true);
    } finally {
      setBusy(false);
      refresh();
    }
  }
  async function decide() {
    if (!confirm || !run?.proposal || !capability.current) return;
    setBusy(true);
    setError(undefined);
    try {
      const value = await api.post<Run>(
        `/api/v1/agent/runs/${run.run_id}/decisions`,
        {
          proposal_id: run.proposal.id,
          proposal_version: run.proposal.version,
          content_digest: run.proposal.content_digest,
          decision: confirm,
          ...(confirm === 'EDIT_AND_REVIEW' ? { edited_content: edited } : {}),
        },
        { 'X-Workflow-Token': capability.current },
      );
      setRun(value);
      setConfirm(undefined);
      setEdited(undefined);
    } catch (caught) {
      setError(caught);
      setConfirm(undefined);
      // A response can be lost after commit. Inspect, never create/retry a write automatically.
      try {
        setRun(
          await api.request<Run>(`/api/v1/agent/runs/${run.run_id}`, {
            headers: { 'X-Workflow-Token': capability.current },
          }),
        );
      } catch {
        /* Keep the original failure visible. */
      }
    } finally {
      setBusy(false);
      refresh();
    }
  }
  async function resume() {
    if (!run || !capability.current) return;
    setBusy(true);
    setError(undefined);
    try {
      setRun(
        await api.post<Run>(`/api/v1/agent/runs/${run.run_id}/resume`, undefined, {
          'X-Workflow-Token': capability.current,
        }),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
      refresh();
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="HUMAN-IN-THE-LOOP"
        title="AI 整理草案，你决定下一步"
        subtitle="真实 LangGraph 状态与 PostgreSQL Checkpoint。没有自动批准，也不展示虚构的思考过程。"
      />
      <ErrorBox error={error} />
      {mockMode && <div className="notice">MOCK · 当前是确定性测试推理，不是真实模型生成。</div>}
      {!enabled && <div className="notice">当前模式未启用 Agent，工作流不会运行。</div>}
      <div className="agent-layout">
        <aside>
          <section className="panel">
            <h2>创建工作流</h2>
            <form onSubmit={create}>
              <DocumentSelect documents={documents} value={scope} onChange={setScope} />
              <label>
                研究任务要求
                <textarea
                  required
                  maxLength={1000}
                  rows={4}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="根据这份科研文档整理实验任务。"
                />
              </label>
              <button className="primary wide" disabled={!enabled || busy || uncertain}>
                {busy ? '等待服务器执行…' : '生成任务草案'}
              </button>
            </form>
            {busy && <Loading text="正在执行请求。未提供实时节点进度，请勿重复创建。" />}
            {uncertain && (
              <div className="notice">
                创建结果不确定，已禁止直接重建。请刷新本人列表，打开已有工作流核对。
              </div>
            )}
          </section>
          <div className="section-heading">
            <h3>本人近期工作流</h3>
            <button className="text-button" onClick={refresh}>
              刷新
            </button>
          </div>
          <div className="run-list">
            {summaries.map((item) => (
              <button
                className={item.run_id === runId ? 'selected' : ''}
                key={item.run_id}
                onClick={() => {
                  location.hash = `agent/${item.run_id}`;
                  setRunId(item.run_id);
                  setRecoveryRevision((value) => value + 1);
                  setPolling(true);
                }}
              >
                <strong>{item.query}</strong>
                <Badge tone={item.status === 'AWAITING_APPROVAL' ? 'amber' : ''}>
                  {labels[item.status]}
                </Badge>
                <small>{date(item.updated_at)}</small>
              </button>
            ))}
            {!summaries.length && <p className="muted">尚未读取到本人工作流。</p>}
          </div>
        </aside>
        <section>
          {run ? (
            <div className="panel workflow-detail">
              <div className="section-heading">
                <Badge
                  tone={
                    run.status === 'AWAITING_APPROVAL'
                      ? 'amber'
                      : run.status === 'COMPLETED'
                        ? 'green'
                        : ''
                  }
                >
                  {labels[run.status]}
                </Badge>
                <button
                  onClick={() => {
                    void inspect();
                  }}
                  disabled={busy}
                >
                  查询最新状态
                </button>
              </div>
              <h2>工作流记录</h2>
              <code className="run-id">{run.run_id}</code>
              <dl className="source-meta">
                <dt>Checkpoint</dt>
                <dd>{run.checkpoint_status}</dd>
                <dt>后端 next nodes</dt>
                <dd>{run.next_nodes.join(' → ') || '当前无待执行节点'}</dd>
                <dt>审批截止</dt>
                <dd>{date(run.expires_at)}</dd>
              </dl>
              {run.error_code && (
                <div className="notice error">
                  执行失败：{run.error_code}。先核对现有状态，再恢复同一工作流。
                </div>
              )}
              {run.status === 'RUNNING' && (
                <button onClick={() => setPolling((old) => !old)}>
                  {polling ? '停止自动刷新' : '恢复自动刷新'}
                </button>
              )}
              <p className="muted">停止浏览器等待或刷新，不代表后端业务已取消。</p>
              {run.proposal ? (
                <>
                  <div className="section-heading">
                    <h2>
                      任务草案 <span className="muted">{run.proposal.content.tasks.length} 项</span>
                    </h2>
                    <Badge>
                      草案 v{run.proposal.version} · {run.proposal.status}
                    </Badge>
                  </div>
                  {run.proposal.content.tasks.map((task, index) => (
                    <article className="draft-item" key={index}>
                      <span className="draft-number">{String(index + 1).padStart(2, '0')}</span>
                      <div>
                        <h3>{task.title}</h3>
                        <Badge>
                          {task.basis === 'documented' ? '原文要求' : '模型建议 · 需判断'}
                        </Badge>
                        <p className="prewrap">{task.description}</p>
                        <small>依据 {task.evidence_id}</small>
                        <blockquote>{task.source_quote}</blockquote>
                      </div>
                    </article>
                  ))}
                  <h3>草案的原始依据</h3>
                  <Sources citations={run.proposal.evidence} documents={documents} />
                  {run.status === 'AWAITING_APPROVAL' ? (
                    <div className="approval-zone">
                      <strong>此草案尚未写入正式任务</strong>
                      <p>批准将创建正式任务；修改会产生新版本，必须再次审阅。</p>
                      <div className="button-row">
                        <button
                          className="danger"
                          disabled={busy}
                          onClick={() => setConfirm('REJECT')}
                        >
                          拒绝草案
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => {
                            setEdited(structuredClone(run.proposal!.content));
                            setConfirm('EDIT_AND_REVIEW');
                          }}
                        >
                          修改后重新审阅
                        </button>
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() => setConfirm('APPROVE')}
                        >
                          审阅并批准
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="notice">
                      当前状态不允许提交新审批。
                      {run.approval &&
                        `已记录 ${run.approval.decision} / ${run.approval.execution_status}`}
                    </div>
                  )}
                </>
              ) : (
                <Empty title="当前没有任务草案">
                  证据不足、执行失败或尚在运行时，不会补造草案。
                </Empty>
              )}
              {run.status === 'FAILED' && (
                <button
                  disabled={busy}
                  onClick={() => {
                    void resume();
                  }}
                >
                  恢复此工作流（不新建）
                </button>
              )}
              {run.status === 'COMPLETED' && (
                <button
                  className="primary"
                  onClick={() => {
                    location.hash = 'tasks';
                  }}
                >
                  查看正式任务 →
                </button>
              )}
            </div>
          ) : runId && enabled ? (
            error ? (
              <button onClick={() => setRecoveryRevision((value) => value + 1)}>
                重新验证并读取
              </button>
            ) : (
              <Loading text="正在验证拥有者并读取持久化工作流…" />
            )
          ) : (
            <div className="panel">
              <Empty title="从一份真实文档开始">
                选择文档范围，生成带依据的任务草案；你可以批准、拒绝，或修改后重新审阅。
              </Empty>
            </div>
          )}
        </section>
      </div>
      {confirm && run?.proposal && (
        <div className="modal-backdrop">
          <section
            className="panel modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="decision-title"
          >
            <Badge>EXPLICIT HUMAN DECISION</Badge>
            <h2 id="decision-title">
              {confirm === 'APPROVE'
                ? '确认批准并写入正式任务？'
                : confirm === 'REJECT'
                  ? '确认拒绝此草案？'
                  : '修改草案，提交后必须重新审批'}
            </h2>
            <p>
              工作流 {run.run_id} · 草案 v{run.proposal.version} ·{' '}
              {run.proposal.content.tasks.length} 项
            </p>
            {confirm === 'EDIT_AND_REVIEW' && edited ? (
              edited.tasks.map((task, index) => (
                <div className="edit-item" key={index}>
                  <label>
                    任务 {index + 1} 标题
                    <input
                      value={task.title}
                      maxLength={200}
                      onChange={(e) =>
                        setEdited({
                          tasks: edited.tasks.map((item, i) =>
                            i === index ? { ...item, title: e.target.value } : item,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    任务 {index + 1} 描述
                    <textarea
                      value={task.description}
                      readOnly={task.basis === 'documented'}
                      onChange={(e) =>
                        setEdited({
                          tasks: edited.tasks.map((item, i) =>
                            i === index ? { ...item, description: e.target.value } : item,
                          ),
                        })
                      }
                    />
                  </label>
                  <small>原文要求保留精确引用；服务器仍会校验全部证据。</small>
                </div>
              ))
            ) : (
              <ul>
                {run.proposal.content.tasks.map((task, index) => (
                  <li key={index}>{task.title}</li>
                ))}
              </ul>
            )}
            <p className="notice">
              {confirm === 'APPROVE'
                ? '我已核对完整草案与来源，确认这些任务可以正式创建。'
                : confirm === 'REJECT'
                  ? '拒绝不会写入正式任务。'
                  : '本次操作不批准、不创建正式任务。'}
            </p>
            <div className="button-row">
              <button disabled={busy} onClick={() => setConfirm(undefined)}>
                返回检查
              </button>
              <button
                className={confirm === 'REJECT' ? 'danger' : 'primary'}
                disabled={busy}
                onClick={() => {
                  void decide();
                }}
              >
                {busy
                  ? '正在提交…'
                  : confirm === 'APPROVE'
                    ? '确认批准并写入'
                    : confirm === 'REJECT'
                      ? '确认拒绝'
                      : '保存修改并重新审阅'}
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
