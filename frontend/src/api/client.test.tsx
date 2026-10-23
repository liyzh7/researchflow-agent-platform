// P2 synthetic protocol tests, newly composed from the API client contract.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ApiClient, ApiError } from './client';
import { ErrorBox } from '../components/ui';

afterEach(() => vi.restoreAllMocks());

describe('candidate client transport boundary', () => {
  it('keeps an invented in-memory bearer on local API requests', async () => {
    const fetcher = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ candidate_counter: 7 })));
    const client = new ApiClient();
    client.token = 'p2-generated-placeholder-session';
    expect(await client.request('/api/v1/auth/me')).toEqual({ candidate_counter: 7 });
    const options = fetcher.mock.calls[0]?.[1];
    expect(new Headers(options?.headers).get('Authorization')).toBe(
      'Bearer p2-generated-placeholder-session',
    );
    expect(options?.redirect).toBe('error');
    expect(options?.credentials).toBe('omit');
    await expect(client.request('https://p2-fixture.invalid/api/v1/auth/me')).rejects.toThrow();
    await expect(client.request('//p2-fixture.invalid')).rejects.toThrow();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('clears only the active session on an authenticated rejection', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () =>
        new Response(JSON.stringify({ error: { code: 'authentication_required' } }), {
          status: 401,
        }),
    );
    const client = new ApiClient();
    client.onUnauthorized = vi.fn();
    await expect(
      client.post('/api/v1/auth/login', { username: 'p2-invented' }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(client.onUnauthorized).not.toHaveBeenCalled();
    client.token = 'p2-generated-placeholder-session';
    await expect(client.request('/api/v1/auth/me')).rejects.toMatchObject({ status: 401 });
    expect(client.token).toBeNull();
    expect(client.onUnauthorized).toHaveBeenCalledOnce();
  });

  it('escapes freshly constructed markup in exception text', () => {
    const payload = '<svg onload="p2_untrusted()">';
    const rendered = renderToStaticMarkup(<ErrorBox error={new Error(payload)} />);
    expect(rendered).not.toContain('<svg');
    expect(rendered).toContain('&lt;svg');
  });

  it('handles no-content and masks a newly invented transport failure', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockRejectedValueOnce(new TypeError('p2-private-upstream-sentinel'));
    const client = new ApiClient();
    expect(await client.post('/api/v1/auth/logout')).toBeUndefined();
    await expect(client.request('/ready')).rejects.toMatchObject({ code: 'network_error' });
  });
});
