const OWNER_EMAIL = "ascensiondigitalagency@outlook.com";
const OWNER_NAME = "Emma James";
const BACKEND_URL = "https://web-production-fb994.up.railway.app";

function shouldInjectSharedFooter(pathname) {
  return pathname === '/' || pathname === '/index.html';
}

async function withSharedFooter(response, pathname) {
  const type = response.headers.get('content-type') || '';
  if (!type.includes('text/html') || !shouldInjectSharedFooter(pathname)) return response;
  let html = await response.text();
  const alreadyHasFooter = html.includes('id="adg-group-footer"') || html.includes('class="rs-footer"') || html.includes("class='rs-footer'");
  if (!alreadyHasFooter && !html.includes('/adg-footer.js')) {
    html = html.includes('</body>')
      ? html.replace('</body>', '<script src="/adg-footer.js" defer></script></body>')
      : html + '<script src="/adg-footer.js" defer></script>';
  }
  const headers = new Headers(response.headers);
  headers.delete('content-length');
  return new Response(html, { status: response.status, statusText: response.statusText, headers });
}

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

    return withSharedFooter(await env.ASSETS.fetch(request), url.pathname);
  }
};
