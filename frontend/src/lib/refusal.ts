/** Why the terminal socket closed before the shell was there. The server refuses a handshake with one of these
 * codes; without one, the socket either never opened (a proxy that does not pass WebSockets, or nextrmnl not
 * reachable) or dropped on the way. */
export function refusalDetail(code: number, opened: boolean): string {
  switch (code) {
    case 4400:
      return 'bad_target'
    case 4401:
      return 'not_signed_in'
    case 4403:
      return 'foreign_origin'
    case 4404:
      return 'connection_not_found'
    case 4429:
      return 'too_many_sessions'
    default:
      return opened ? 'connection_lost' : 'socket_failed'
  }
}
