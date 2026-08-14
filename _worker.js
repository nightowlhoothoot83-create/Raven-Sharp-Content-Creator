const OWNER_EMAIL = "ascensiondigitalagency@outlook.com";
const OWNER_NAME = "Emma James";
const BACKEND_URL = "https://web-production-fb994.up.railway.app";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname.startsWith('/api/')) {
      const backend = BACKEND_URL.replace(/\/$/, '');
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
