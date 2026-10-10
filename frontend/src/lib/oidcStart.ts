/**
 * The address of a provider's sign-in button. It carries the page the person came for, so a direct link
 * (`/connect/<id>`) survives the way through the provider instead of ending on the start page. The server takes only
 * its own pages (`routers/oidc.safe_next`) and lands on the start page for anything else. An invitation travels
 * along as `invite` (the invitation page's "Continue with …").
 */
export function oidcStartHref(slug: string, pathname = '', invite?: string): string {
  const params = new URLSearchParams()
  if (invite) params.set('invite', invite)
  if (pathname && pathname !== '/' && pathname !== '/login' && !pathname.startsWith('/invite/')) params.set('next', pathname)
  const query = params.toString()
  return `/api/oidc/${encodeURIComponent(slug)}/start${query ? `?${query}` : ''}`
}
