#!/bin/sh

set -e

host="$1"
shift
cmd="$@"

until pg_isready -h "$host" -U "postgres"; do
  >&2 echo "⏳ Waiting for PostgreSQL ($host) to be ready..."
  sleep 2
done

>&2 echo "✅ PostgreSQL is up - executing command"
exec $cmd
