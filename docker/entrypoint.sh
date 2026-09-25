#!/bin/sh
# Container entrypoint.
#
# nextrmnl must not run as root. A data directory mounted from outside carries the rights of the host,
# and those rarely match the user inside the image by chance. So the container briefly fixes the rights
# as root and then drops to the user "nextrmnl". PUID and PGID say which host user owns the files.

set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Port inside the container, default 8000. NEXTRMNL_PORT is for host networking, where the container's port
# is the server's port. Docker does not expand variables in the JSON form of CMD, hence here.
if [ "$1" = "uvicorn" ]; then
    case " $* " in
        *" --port "*) ;;
        *) set -- "$@" --port "${NEXTRMNL_PORT:-8000}" ;;
    esac
fi

# Started without root already ("user:" in the compose file): nothing to fix.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

if [ "$(id -g nextrmnl)" != "$PGID" ]; then
    groupmod -o -g "$PGID" nextrmnl
fi
if [ "$(id -u nextrmnl)" != "$PUID" ]; then
    usermod -o -u "$PUID" nextrmnl
fi

mkdir -p /data

# Only touch it when the owner is wrong. A "chown -R" on every start costs time on large directories.
if [ "$(stat -c %u /data)" != "$PUID" ] || [ "$(stat -c %g /data)" != "$PGID" ]; then
    echo "nextrmnl: adjusting ownership of the data directory to $PUID:$PGID."
    chown -R "$PUID:$PGID" /data
fi

# Owner does not mean writable: on a NAS the directory can belong to the right user and still be closed by
# an access list. Then a long Python error would appear mid-start that nobody reads the cause from. So we
# really write here, as the user that does it later.
if ! gosu nextrmnl sh -c 'touch /data/.write-test' 2>/dev/null; then
    echo "nextrmnl: the data directory is not writable." >&2
    echo "" >&2
    echo "  nextrmnl runs as uid $PUID, gid $PGID and cannot write to the" >&2
    echo "  directory mounted at /data. Nothing has been started." >&2
    echo "" >&2
    echo "  On the host, that directory needs to belong to that user:" >&2
    echo "" >&2
    echo "      sudo chown -R $PUID:$PGID /path/to/your/data" >&2
    echo "      sudo chmod -R u+rwX /path/to/your/data" >&2
    echo "" >&2
    echo "  PUID and PGID are set in your compose file. To find your own," >&2
    echo "  run 'id' on the host and use the uid and gid it reports." >&2
    exit 1
fi
rm -f /data/.write-test

exec gosu nextrmnl "$@"
