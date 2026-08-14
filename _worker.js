const OWNER_EMAIL = "ascensiondigitalagency@outlook.com";
const OWNER_NAME = "Emma James";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname.startsWith('/api/')) {
      const backend = String(env.BACKEND_URL || '').replace(/\/$/, '');
      if (!backend) {
        return new Response(JSON.stringify({
          detail: 'Content Creator backend is not configured. Set BACKEND_URL in Cloudflare Pages environment variables.',
          owner_email: OWNER_EMAIL,
          owner_name: OWNER_NAME
        }), { status: 503, headers: { 'content-type': 'application/json; charset=utf-8' } });
      }

      const target = new URL(url.pathname + url.search, backend + '/');
      const headers = new Headers(request.headers);
      headers.set('host', target.host);
      headers.set('x-forwarded-host', url.host);
      headers.set('x-forwarded-proto', 'https');

      return fetch(new Request(target, {
        method: request.method,
        headers,
        body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
        redirect: 'manual'
      }));
    }

    return env.ASSETS.fetch(request);
  }
};
