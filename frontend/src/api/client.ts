export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly requestId: string | null = null,
  ) {
    super(errorMessage(status, code));
  }
}

export function errorMessage(status: number, code: string): string {
  if (status === 401) return '登录已失效或凭据不正确，请重新登录。';
  if (status === 403) return '没有操作权限，或工作流审批凭据不正确。';
  if (status === 404) return '资源不存在或当前用户无权访问。';
  if (status === 409) return '状态发生冲突，请查询最新状态后再操作，不要重复创建。';
  if (status === 410) return '草案或审批有效期已结束，无法继续批准。';
  if (status === 413) return '请求过大，请缩小文件或输入内容。';
  if (status === 422) return '输入格式不符合要求，请检查字段、文件类型和草案内容。';
  if (status === 429) return '请求过于频繁，请稍后再试。';
  if (code === 'provider_busy') return '资源繁忙：本地模型等待队列已满，请稍后重试。';
  if (status === 504 || code === 'client_timeout')
    return '请求超时。浏览器停止等待不代表后端已取消，请先查询状态。';
  if (code.includes('database')) return '数据库暂时不可用，不代表知识库没有答案。';
  if (code === 'retrieval_unavailable' || code === 'citation_source_unavailable')
    return '检索数据库或引用来源暂时不可用，不是证据不足。';
  if (code.startsWith('embedding_') || code.startsWith('reranker_'))
    return '本地向量或重排模型不可用，请检查运行配置和缓存。';
  if (code === 'agent_disabled' || code === 'rag_disabled' || code === 'rag_not_configured')
    return '当前运行模式未启用此 AI 功能。';
  if (code === 'network_error') return '无法连接服务，请检查本机服务后重试。';
  if (status === 503 && code.includes('provider')) return '模型不可用，请检查本地推理服务。';
  if (status >= 500) return '服务暂时不可用，请保留请求 ID 并查询最新状态。';
  return '请求未完成，请检查运行状态。';
}

export class ApiClient {
  token: string | null = null;
  onUnauthorized: () => void = () => {};
  onRequest: (id: string | null) => void = () => {};

  async request<T>(path: string, options: RequestInit = {}, timeoutMs = 180000): Promise<T> {
    if (!/^\/(api\/v1(?:\/|$)|health$|ready$)/.test(path))
      throw new Error('invalid local API path');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const headers = new Headers(options.headers);
    const requestToken = this.token;
    if (requestToken) headers.set('Authorization', `Bearer ${requestToken}`);
    if (typeof options.body === 'string') headers.set('Content-Type', 'application/json');
    try {
      const response = await fetch(path, {
        ...options,
        headers,
        signal: controller.signal,
        cache: 'no-store',
        credentials: 'omit',
        redirect: 'error',
      });
      const requestId = response.headers.get('X-Request-ID');
      this.onRequest(requestId);
      if (response.status === 204) return undefined as T;
      const value: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        let code = 'http_error';
        if (value && typeof value === 'object' && 'error' in value) {
          const error = value.error;
          if (
            error &&
            typeof error === 'object' &&
            'code' in error &&
            typeof error.code === 'string'
          )
            code = error.code;
        }
        if (response.status === 401 && requestToken && this.token === requestToken) {
          this.token = null;
          this.onUnauthorized();
        }
        throw new ApiError(response.status, code, requestId);
      }
      return value as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(0, controller.signal.aborted ? 'client_timeout' : 'network_error');
    } finally {
      clearTimeout(timer);
    }
  }

  post<T>(path: string, value?: unknown, headers?: HeadersInit): Promise<T> {
    return this.request(
      path,
      { method: 'POST', headers, body: value === undefined ? undefined : JSON.stringify(value) },
      300000,
    );
  }
}
