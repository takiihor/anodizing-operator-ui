#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
usage() { printf 'Usage: %s [PORT] (default: 8871)\n' "${0##*/}"; }
if [[ ${1-} == --help || ${1-} == -h ]]; then
  usage
  exit 0
fi

pid_only=false
if [[ ${1-} == --pid ]]; then
  pid_only=true
  shift
fi
port="${1-8871}"
if (( $# > 1 )) || [[ ! $port =~ ^[1-9][0-9]{0,4}$ ]] || (( port > 65535 )); then
  usage >&2
  exit 2
fi

operator_pid() {
  local listeners bound processes pid
  local -a argv
  if ! listeners="$(ss -H -ltnp "src 127.0.0.1 and sport = :$port")"; then
    printf 'Could not inspect port %s.\n' "$port" >&2
    return 3
  fi
  if [[ -z "$listeners" ]]; then
    return 1
  fi
  read -r _ _ _ bound _ processes <<< "$listeners"
  if [[ $bound == "127.0.0.1:$port" && $processes =~ pid=([0-9]+) ]]; then
    pid="${BASH_REMATCH[1]}"
    if [[ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" == "$root" ]] \
      && mapfile -d '' -t argv 2>/dev/null <"/proc/$pid/cmdline" \
      && [[ "${argv[1]-}" == -m && "${argv[2]-}" == backend.server ]]; then
      printf '%s\n' "$pid"
      return 0
    fi
  fi
  printf 'Port %s is in use by a different process.\n' "$port" >&2
  return 2
}

if $pid_only; then
  operator_pid
  exit
fi

state="$root/.runtime"
mkdir -p -- "$state"
exec 9>"$state/launcher.lock"
flock -x 9

if pid="$(operator_pid)"; then
  kill -TERM "$pid"
else
  status=$?
  if (( status == 1 )); then
    printf 'Operator UI is already stopped on port %s.\n' "$port"
    exit 0
  fi
  exit "$status"
fi

for (( attempt = 0; attempt < 50; attempt++ )); do
  if current_pid="$(operator_pid 2>/dev/null)"; then
    if [[ "$current_pid" != "$pid" ]]; then
      break
    fi
  else
    status=$?
    if (( status == 3 )); then
      printf 'Could not verify that Operator UI stopped on port %s.\n' "$port" >&2
      exit 1
    fi
    break
  fi
  sleep 0.1
done

if (( attempt == 50 )); then
  printf 'Operator UI is still running on port %s (PID %s).\n' "$port" "$pid" >&2
  exit 1
fi
printf 'Operator UI stopped on port %s.\n' "$port"
