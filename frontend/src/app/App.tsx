import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiClient } from '../api/client';
import type { Document, Info, Page, Ready, RunSummary, Session, Task, User } from '../api/types';
import { Badge, date, Empty, ErrorBox, Icon, Loading } from '../components/ui';
import { DocumentsPage } from '../pages/Documents';
import { QuestionsPage } from '../pages/Questions';
import { AgentPage } from '../pages/Agent';

const navigation = [
  ['home', '工作台'],
  ['docs', '科研知识库'],
  ['chat', '智能问答'],
  ['agent', 'Agent 工作流'],
  ['tasks', '科研任务'],
  ['status', '系统状态'],
] as const;
type View = (typeof navigation)[number][0];
const readView = (): View => {
  const candidate = location.hash.slice(1).split('/')[0];
  return navigation.find(([key]) => key === candidate)?.[0] ?? 'home';
};

export function App() {
  const api = useMemo(() => new ApiClient(), []);
  const [user, setUser] = useState<User | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  api.onUnauthorized = () => {
    setUser(null);
    setNotice('登录已过期或被撤销，请重新登录。');
  };
  useEffect(() => {
    if (!expiresAt) return;
    const timer = setTimeout(
      () => {
        api.token = null;
        setUser(null);
        setNotice('Session 已到期，请重新登录。');
      },
      Math.max(0, Date.parse(expiresAt) - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [api, expiresAt]);
  async function login(username: string, password: string) {
    const session = await api.post<Session>('/api/v1/auth/login', { username, password });
    api.token = session.access_token;
    let identity: User;
    try {
      identity = await api.request<User>('/api/v1/auth/me');
    } catch (error) {
      api.token = null;
      throw error;
    }
    setExpiresAt(session.expires_at);
    setUser(identity);
    setNotice('');
  }
  async function logout() {
    try {
      await api.post('/api/v1/auth/logout');
    } finally {
      api.token = null;
      setUser(null);
      setExpiresAt(null);
      setNotice('已退出本地工作台。');
    }
  }
  return user ? (
    <Workspace key={user.id} api={api} user={user} logout={logout} />
  ) : (
    <Login login={login} notice={notice} />
  );
}

function Login({
  login,
  notice,
}: {
  login: (name: string, password: string) => Promise<void>;
  notice: string;
}) {
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await login(name, password);
    } catch (caught) {
      setError(caught);
    } finally {
      setPassword('');
      setBusy(false);
    }
  }
  return (
    <main className="login-layout">
      <section className="login-story">
        <a className="brand" href="#home">
          <span className="brand-mark">
            R<span>f</span>
          </span>
          <span>
            ResearchFlow<small>RESEARCH WORKSPACE</small>
          </span>
        </a>
        <div>
          <span className="eyebrow">从知识，到行动</span>
          <h1>
            每一个科研决定，
            <br />
            都有据可循。
          </h1>
          <p>
            连接文献、证据与任务。让 AI 整理线索，
            <br />
            让你保留最终决定权。
          </p>
          <div className="method">
            <span>
              01<small>整理知识</small>
            </span>
            <i>→</i>
            <span>
              02<small>验证证据</small>
            </span>
            <i>→</i>
            <span>
              03<small>审阅行动</small>
            </span>
          </div>
        </div>
        <small>本地运行 · 用户隔离 · 人工审批</small>
      </section>
      <section className="login-form">
        <div className="login-card">
          <Badge tone="green">LOCAL WORKSPACE</Badge>
          <h2>欢迎回到工作台</h2>
          <p className="muted">使用通过本地 CLI 创建的账号登录。</p>
          {notice && (
            <p role="status" className="notice">
              {notice}
            </p>
          )}
          <form onSubmit={submit}>
            <label>
              用户名
              <input
                autoComplete="username"
                required
                maxLength={64}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              密码
              <input
                type="password"
                autoComplete="current-password"
                required
                maxLength={256}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <ErrorBox error={error} />
            <button className="primary wide" disabled={busy}>
              {busy ? '正在验证身份…' : '进入科研工作台'}
              <Icon name="arrow" />
            </button>
          </form>
          <div className="login-help">
            没有默认账号，也不开放匿名注册。
            <br />
            凭据仅保存在本次页面内存，刷新后需重新登录。
          </div>
        </div>
      </section>
    </main>
  );
}

function Workspace({
  api,
  user,
  logout,
}: {
  api: ApiClient;
  user: User;
  logout: () => Promise<void>;
}) {
  const [view, setView] = useState<View>(readView);
  const [documents, setDocuments] = useState<Page<Document>>();
  const [runs, setRuns] = useState<Page<RunSummary>>();
  const [tasks, setTasks] = useState<Page<Task>>();
  const [info, setInfo] = useState<Info>();
  const [ready, setReady] = useState<Ready>();
  const [error, setError] = useState<unknown>();
  const [requestId, setRequestId] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  api.onRequest = setRequestId;
  const refresh = () => setRevision((v) => v + 1);
  useEffect(() => {
    const change = () => setView(readView());
    window.addEventListener('hashchange', change);
    return () => window.removeEventListener('hashchange', change);
  }, []);
  useEffect(() => {
    let current = true;
    setError(undefined);
    void (async () => {
      try {
        const [docs, configuration, readiness] = await Promise.all([
          api.request<Page<Document>>('/api/v1/documents?limit=100'),
          api.request<Info>('/api/v1/info'),
          api.request<Ready>('/ready'),
        ]);
        if (!current) return;
        setDocuments(docs);
        setInfo(configuration);
        setReady(readiness);
        if (configuration.agent_enabled) {
          const [workflows, taskList] = await Promise.all([
            api.request<Page<RunSummary>>('/api/v1/agent/runs?limit=100'),
            api.request<Page<Task>>('/api/v1/tasks?limit=100'),
          ]);
          if (current) {
            setRuns(workflows);
            setTasks(taskList);
          }
        }
      } catch (caught) {
        if (current) setError(caught);
      }
    })();
    return () => {
      current = false;
    };
  }, [api, revision]);
  const go = (target: View) => {
    location.hash = target;
  };
  return (
    <div className="workspace">
      <aside className="sidebar">
        <a className="brand" href="#home">
          <span className="brand-mark">
            R<span>f</span>
          </span>
          <span>
            ResearchFlow<small>科研知识与任务工作台</small>
          </span>
        </a>
        <div className="workspace-label">
          个人研究空间 <Badge>LOCAL</Badge>
        </div>
        <nav aria-label="主导航">
          {navigation.map(([key, label], index) => (
            <a
              key={key}
              aria-label={label}
              href={`#${key}`}
              className={view === key ? 'active' : ''}
              aria-current={view === key ? 'page' : undefined}
            >
              <Icon name={key} />
              <span>{label}</span>
              <small>{String(index + 1).padStart(2, '0')}</small>
            </a>
          ))}
        </nav>
        <div className="sidebar-note">
          <Icon name="agent" />
          <strong>你的判断，始终在环</strong>
          <p>Agent 草案只有经过明确审批，才会写入正式科研任务。</p>
        </div>
        <div className="identity">
          <span className="avatar">{user.username[0]?.toUpperCase()}</span>
          <div>
            <strong>{user.username}</strong>
            <small>{user.is_admin ? '本地管理员' : '科研工作空间'}</small>
          </div>
          <button
            className="text-button"
            onClick={() => {
              void logout().catch(() => {});
            }}
            aria-label="注销"
          >
            退出
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span>
            个人研究空间 <span className="slash">/</span>{' '}
            {navigation.find(([key]) => key === view)?.[1]}
          </span>
          <div>
            <Badge tone={ready?.status === 'ready' ? 'green' : ''}>
              {ready?.status === 'ready' ? '数据库已就绪' : '就绪状态未确认'}
            </Badge>
            <span className="top-date">
              {new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(new Date())}
            </span>
            <button
              className="mobile-logout text-button"
              aria-label="注销"
              onClick={() => {
                void logout().catch(() => {});
              }}
            >
              退出
            </button>
          </div>
        </header>
        <main className="content">
          <ErrorBox error={error} />
          {view === 'home' && (
            <>
              <div className="page-title">
                <div>
                  <span className="eyebrow">RESEARCH OVERVIEW</span>
                  <h1>专注研究，让知识开始流动。</h1>
                  <p>欢迎，{user.username}。从一份文档开始，走向有证据的下一步。</p>
                </div>
                <button className="primary" onClick={() => go('docs')}>
                  <Icon name="plus" />
                  导入科研文档
                </button>
              </div>
              <section className="overview-hero">
                <div>
                  <Badge tone="green">EVIDENCE → HUMAN → ACTION</Badge>
                  <h2>
                    让每一步行动，
                    <br />
                    回到真实的研究依据。
                  </h2>
                  <p>在自己的知识库中提问，检查原文来源，再审阅 Agent 整理的任务。</p>
                  <button className="light-button" onClick={() => go('chat')}>
                    开始证据问答 <Icon name="arrow" />
                  </button>
                </div>
                <div className="flow-note">
                  <span>01 / KNOWLEDGE</span>
                  <strong>科研文档</strong>
                  <i>↓</i>
                  <span>02 / EVIDENCE</span>
                  <strong>有来源的回答</strong>
                  <i>↓</i>
                  <span>03 / YOUR DECISION</span>
                  <strong>审阅与任务</strong>
                </div>
              </section>
              <section className="stat-grid">
                {[
                  ['科研文档', documents?.total, 'docs'],
                  ['Agent 工作流', runs?.total, 'agent'],
                  ['正式科研任务', tasks?.total, 'tasks'],
                ].map(([label, count, target]) => (
                  <button className="stat-card" key={label} onClick={() => go(target as View)}>
                    <span>{label}</span>
                    <strong>{count ?? '—'}</strong>
                    <small>
                      {count === undefined ? '当前未启用或尚未读取' : '来自当前用户的数据库记录'}{' '}
                      <Icon name="arrow" size={16} />
                    </small>
                  </button>
                ))}
              </section>
              <div className="section-heading">
                <h2>最近的科研文档</h2>
                <button className="text-button" onClick={() => go('docs')}>
                  查看知识库 →
                </button>
              </div>
              <section className="panel">
                {documents ? (
                  documents.items.length ? (
                    documents.items.slice(0, 4).map((doc) => (
                      <div className="list-row" key={doc.id}>
                        <span className="file-icon">
                          <Icon name="docs" />
                        </span>
                        <div className="grow">
                          <strong>{doc.title}</strong>
                          <small>
                            {doc.document_key} · v{doc.current_version?.version_number ?? '—'}
                          </small>
                        </div>
                        <span className="muted">{date(doc.updated_at)}</span>
                      </div>
                    ))
                  ) : (
                    <Empty title="研究空间准备就绪">
                      上传第一份 TXT 或 Markdown，建立属于你的知识库。
                    </Empty>
                  )
                ) : (
                  <Loading />
                )}
              </section>
            </>
          )}
          {view === 'docs' && (
            <DocumentsPage
              api={api}
              documents={documents?.items ?? []}
              info={info}
              refresh={refresh}
            />
          )}
          {view === 'chat' && (
            <QuestionsPage
              api={api}
              documents={documents?.items ?? []}
              enabled={info?.rag_enabled ?? false}
            />
          )}
          {view === 'agent' && (
            <AgentPage
              api={api}
              documents={documents?.items ?? []}
              summaries={runs?.items ?? []}
              enabled={info?.agent_enabled ?? false}
              mockMode={info?.provider_mode === 'mock'}
              refresh={refresh}
            />
          )}
          {view === 'tasks' && (
            <>
              <PageTitle
                eyebrow="RESEARCH TASKS"
                title="把决定落实为行动"
                subtitle="这里只展示经过审批、已写入 PostgreSQL 的正式任务。"
              />
              {!info?.agent_enabled ? (
                <Empty title="当前未启用 Agent">基础演示模式不生成科研任务。</Empty>
              ) : !tasks ? (
                <Loading />
              ) : !tasks.items.length ? (
                <Empty title="还没有正式任务">
                  草案不是任务。请先在 Agent 工作流中审阅并批准。
                </Empty>
              ) : (
                <div className="task-grid">
                  {tasks.items.map((task) => (
                    <article className="panel task-card" key={task.id}>
                      <div className="section-heading">
                        <Badge>{task.status}</Badge>
                        <small>{date(task.created_at)}</small>
                      </div>
                      <h2>{task.title}</h2>
                      <p className="prewrap">{task.description}</p>
                      <SourcesForTask task={task} />
                      <small>审批记录 {task.approval_id}</small>
                      <button
                        className="text-button"
                        onClick={() => {
                          location.hash = `agent/${task.workflow_id}`;
                        }}
                      >
                        查看关联工作流 →
                      </button>
                    </article>
                  ))}
                </div>
              )}
            </>
          )}
          {view === 'status' && (
            <>
              <PageTitle
                eyebrow="LOCAL OBSERVABILITY"
                title="看见实际运行状态"
                subtitle="本地状态快照，不是生产监控平台。不主动探测或下载模型。"
              />
              <section className="panel status-grid">
                <div>
                  <small>API 配置</small>
                  <h2>{info ? `ResearchFlow ${info.version}` : '未取得'}</h2>
                  <p>{info?.environment ?? '未知环境'}</p>
                </div>
                <div>
                  <small>PostgreSQL / pgvector</small>
                  <h2>{ready?.status === 'ready' ? '已就绪' : '未确认'}</h2>
                  <p>pgvector {ready?.pgvector_version ?? '未知'}</p>
                </div>
                <div>
                  <small>LLM Provider 模式</small>
                  <h2>
                    {info?.provider_mode === 'mock'
                      ? 'Mock 测试模式'
                      : info?.provider_mode === 'openai_compatible'
                        ? '本地兼容 API'
                        : '未知'}
                  </h2>
                  <p>推理服务在线状态需以实际调用为准</p>
                </div>
                <div>
                  <small>功能配置</small>
                  <h2>
                    RAG {info?.rag_enabled ? '启用' : '关闭'} / Agent{' '}
                    {info?.agent_enabled ? '启用' : '关闭'}
                  </h2>
                  <p>身份：{user.username}</p>
                </div>
              </section>
              <div className="notice">
                最近请求 ID：<code>{requestId ?? '尚未取得'}</code>
                <br />
                不展示其他用户日志、完整审计库或密钥。
              </div>
              <button onClick={refresh}>重新读取状态</button>
            </>
          )}
        </main>
        <footer>
          ResearchFlow <span>本地科研工作台 · AI 输出需要核验，正式操作需要你的确认。</span>
        </footer>
      </div>
    </div>
  );
}
function SourcesForTask({ task }: { task: Task }) {
  return (
    <details>
      <summary>
        来源：{task.source_evidence.document_key} · v{task.source_evidence.version_number}
      </summary>
      <blockquote>{task.source_evidence.excerpt}</blockquote>
      <small>Segment {task.source_evidence.segment_id}</small>
    </details>
  );
}
export function PageTitle({
  eyebrow,
  title,
  subtitle,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="page-title">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
    </div>
  );
}
