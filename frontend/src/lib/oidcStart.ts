/**
 * The address of the sign-in button for the provider. It carries the page the person came for, so a direct
 * link (`/connect/<id>`) survives the way through the provider instead of ending on the start page. The server
 * takes only its own pages (`oidc.safe_next`) and lands on the start page for anything else.
 */
export function oidcStartHref(pathname: string): string {
  if (!pathname || pathname === '/' || pathname === '/login') return '/api/oidc/start'
  return `/api/oidc/start?next=${encodeURIComponent(pathname)}`
}
