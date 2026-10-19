#!/bin/sh
set -eu
root=${1:?Exact owned cluster path required}
case "$root" in /tmp/asteria-concurrency-cross-*) ;; *) exit 2;; esac
case "${root#/tmp/}" in *[!a-zA-Z0-9_-]*) exit 2;; esac
test "$(realpath -- "$root")" = "$root"
export LD_LIBRARY_PATH="$root/runtime/usr/lib/x86_64-linux-gnu"
if test -f "$root/pgdata/postmaster.pid"; then
    "$root/runtime/usr/lib/postgresql/16/bin/pg_ctl" -D "$root/pgdata" -m fast -t 15 -w stop
fi
rm -rf -- "$root"
test ! -e "$root"
echo isolated_cluster_removed
