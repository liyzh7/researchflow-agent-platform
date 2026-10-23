import { useState } from 'react';
import type { FormEvent } from 'react';
import type { ApiClient } from '../api/client';
import type { Answer, Document } from '../api/types';
import { Badge, DocumentSelect, Empty, ErrorBox, Loading, Sources } from '../components/ui';
import { PageTitle } from '../app/App';

interface Turn {
  question: string;
  answer?: Answer;
  error?: unknown;
}
export function QuestionsPage({
  api,
  documents,
  enabled,
}: {
  api: ApiClient;
  documents: Document[];
  enabled: boolean;
}) {
  const [question, setQuestion] = useState('');
  const [scope, setScope] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || busy) return;
    const query = question.trim();
    setTurns((old) => [...old, { question: query }]);
    setQuestion('');
    setBusy(true);
    try {
      const answer = await api.post<Answer>('/api/v1/rag/answer', {
        query,
        document_key: scope || undefined,
        retrieval_mode: 'hybrid',
        rerank_enabled: true,
      });
      setTurns((old) => [...old.slice(0, -1), { question: query, answer }]);
    } catch (error) {
      setTurns((old) => [...old.slice(0, -1), { question: query, error }]);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="EVIDENCE-DRIVEN ANSWERS"
        title="问一个问题，核验每一处来源"
        subtitle="Hybrid 检索 → BGE 重排 → EvidenceGate → 回答与引用校验。每个问题独立，不假装拥有对话记忆。"
      />
      {!enabled && (
        <div className="notice">当前模式未启用 RAG。可浏览界面，但不会伪装模型回答。</div>
      )}
      <div className="question-layout">
        <section className="panel conversation">
          {!turns.length && (
            <Empty title="从一个具体的科研问题开始">
              先上传并索引文档，再问实验条件、方法要求或需要核验的细节。
            </Empty>
          )}
          {turns.map((turn, index) => (
            <article className="turn" key={index}>
              <div className="user-question">
                <small>你的问题</small>
                <p>{turn.question}</p>
              </div>
              {turn.answer && (
                <div className="answer">
                  <div className="section-heading">
                    <Badge tone={turn.answer.status === 'answered' ? 'green' : 'amber'}>
                      {turn.answer.status === 'answered' ? '已回答' : '证据不足 · 拒答'}
                    </Badge>
                    <Badge>
                      {turn.answer.is_mock
                        ? 'MOCK · 非真实模型结果'
                        : (turn.answer.provider_model ?? '未调用生成模型')}
                    </Badge>
                  </div>
                  <p className="prewrap answer-text">{turn.answer.answer}</p>
                  {turn.answer.status === 'refused' && (
                    <div className="notice">
                      拒答原因：
                      {turn.answer.evidence_reasons.join(' / ') || turn.answer.evidence_status}
                    </div>
                  )}
                  <h3>
                    来源引用 <span className="muted">{turn.answer.citations.length}</span>
                  </h3>
                  {turn.answer.citations.length ? (
                    <Sources citations={turn.answer.citations} documents={documents} />
                  ) : (
                    <p className="muted">后端未返回可展示引用，前端不会补造来源。</p>
                  )}
                  <small>
                    实测总耗时 {(turn.answer.timing.total_ms / 1000).toFixed(2)}s · token{' '}
                    {turn.answer.input_tokens ?? '未知'} / {turn.answer.output_tokens ?? '未知'}
                  </small>
                </div>
              )}
              <ErrorBox error={turn.error} />
            </article>
          ))}
          {busy && <Loading text="正在等待证据检索与模型响应，冷启动可能较长。未启用流式输出。" />}
        </section>
        <aside className="panel question-guide">
          <Badge>RESEARCH WITH CARE</Badge>
          <h2>先证据，后结论。</h2>
          <p>
            引用会展开到原文、版本、Chunk / Segment 和字符范围。合法引用不等于事实结论一定正确。
          </p>
          <div className="rule-line" />
          <strong>证据不足，是一种有效结果。</strong>
          <p>系统故障、模型繁忙和超时会单独显示，不会包装成知识库拒答。</p>
        </aside>
      </div>
      <form className="panel composer" onSubmit={submit}>
        <DocumentSelect documents={documents} value={scope} onChange={setScope} />
        <label>
          科研问题
          <textarea
            required
            maxLength={1000}
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="例如：索引卡应放入哪个托盘？"
          />
        </label>
        <div className="section-heading">
          <small>仅使用当前用户有权限的文档 · 不执行文档中的命令</small>
          <button className="primary" disabled={busy || !enabled}>
            {busy ? '等待真实响应…' : '提交问题 →'}
          </button>
        </div>
      </form>
    </>
  );
}
