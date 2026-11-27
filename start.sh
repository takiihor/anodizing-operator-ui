#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
usage() { printf 'Usage: %s [PORT] (default: 8871)\n' "${0##*/}"; }
if [[ ${1-} == --help || ${1-} == -h ]]; then
  usage
  exit 0
fi

port="${1-8871}"
if (( $# > 1 )) || [[ ! $port =~ ^[1-9][0-9]{0,4}$ ]] || (( port > 65535 )); then
  usage >&2
  exit 2
fi

url="http://127.0.0.1:$port/"
state="$root/.runtime"
log="$state/operator-$port.log"
mkdir -p -- "$state"
exec 9>"$state/launcher.lock"
flock -x 9

if pid="$("$root/kill.sh" --pid "$port")"; then
  printf 'Operator UI is already running at %s (PID %s).\n' "$url" "$pid"
  exit 0
else
  status=$?
  if (( status != 1 )); then
    exit "$status"
  fi
fi

frontend="$root/frontend"
index="$frontend/dist/index.html"
if [[ ! -f "$index" ]] \
  || [[ -z $(find "$frontend/dist/assets" -type f -name '*.js' -print -quit 2>/dev/null) ]] \
  || [[ -z $(find "$frontend/dist/assets" -type f -name '*.css' -print -quit 2>/dev/null) ]] \
  || [[ -n $(find "$frontend/src" "$frontend/index.html" "$frontend/package.json" \
          "$frontend/package-lock.json" "$frontend/tsconfig.json" "$frontend/vite.config.ts" \
          -type f -newer "$index" -print -quit) ]]; then
  if ! command -v npm >/dev/null; then
    printf 'Install Node.js and npm to build the Operator UI.\n' >&2
    exit 1
  fi
  if [[ ! -x "$frontend/node_modules/.bin/vite" || ! -x "$frontend/node_modules/.bin/vue-tsc" \
        || "$frontend/package-lock.json" -nt "$frontend/node_modules/.package-lock.json" ]]; then
    npm --prefix "$frontend" ci
  fi
  npm --prefix "$frontend" run build
fi

cd -- "$root"
nohup python3 -m backend.server --port "$port" </dev/null >>"$log" 2>&1 9>&- &
pid=$!

for (( attempt = 0; attempt < 50; attempt++ )); do
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  if curl --noproxy '*' --fail --silent --max-time 1 --output /dev/null "$url" \
     && [[ "$("$root/kill.sh" --pid "$port" 2>/dev/null)" == "$pid" ]]; then
    printf 'Operator UI is running at %s (PID %s).\nLog: %s\n' "$url" "$pid" "$log"
    exit 0
  fi
  sleep 0.1
done

kill -TERM "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
printf 'Operator UI failed to start on port %s. See %s\n' "$port" "$log" >&2
tail -n 12 -- "$log" >&2
exit 1
