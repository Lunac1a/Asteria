#!/bin/sh
# Dedicated disposable cluster, never discovers or touches development PGDATA.
set -eu
ROOT=${1:?Pass a new /tmp/asteria-concurrency-NAME directory}
ACTION=${2:-start}
case "$ROOT" in /tmp/asteria-concurrency-*) ;; *) exit 2;; esac
case "${ROOT#/tmp/}" in *[!a-zA-Z0-9_-]*) exit 2;; esac
export LD_LIBRARY_PATH="$ROOT/runtime/usr/lib/x86_64-linux-gnu"
BIN="$ROOT/runtime/usr/lib/postgresql/16/bin"
if [ "$ACTION" = stop ]; then
    "$BIN/pg_ctl" -D "$ROOT/pgdata" -m fast -t 15 -w stop
    exit
fi
test "$ACTION" = start
mkdir "$ROOT"
cd "$ROOT"
apt-get download postgresql-16 postgresql-client-16 libpq5
mkdir runtime
for package in ./*.deb; do dpkg-deb -x "$package" runtime; done
"$BIN/initdb" -D "$ROOT/pgdata" -U concurrency_test -A trust --no-locale -E UTF8
cat >> "$ROOT/pgdata/postgresql.conf" <<EOF
port = 55489
listen_addresses = '127.0.0.1'
unix_socket_directories = '$ROOT'
log_statement = 'all'
EOF
"$BIN/pg_ctl" -D "$ROOT/pgdata" -l "$ROOT/postgres.log" -t 15 -w start
"$BIN/createdb" -h 127.0.0.1 -p 55489 -U concurrency_test workspace_concurrency_test
