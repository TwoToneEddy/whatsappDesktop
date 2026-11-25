#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="${WAT_CONTAINER_RUNTIME:-}"
if [[ -z "$runtime" ]]; then
    if command -v podman >/dev/null 2>&1; then
        runtime=podman
    elif command -v docker >/dev/null 2>&1; then
        runtime=docker
    else
        echo 'Podman or Docker is required. Run this launcher on your host with an available container runtime.' >&2
        exit 1
    fi
fi
if [[ "$runtime" != podman && "$runtime" != docker ]]; then
    echo 'WAT_CONTAINER_RUNTIME must be podman or docker.' >&2
    exit 1
fi

label_mode="${WAT_SELINUX_LABEL:-private}"
case "$label_mode" in
    private|disable) ;;
    *)
        echo 'WAT_SELINUX_LABEL must be private or disable.' >&2
        exit 1
        ;;
esac

data_dir="${WAT_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/whatsapp-terminal}"
umask 077
mkdir -p -- "$data_dir"
data_dir="$(cd -- "$data_dir" && pwd)"
image=localhost/whatsapp-terminal:local
"$runtime" build --tag "$image" "$project_dir"

options=(run --rm -it --env "TERM=${TERM:-xterm-256color}")
if [[ "$runtime" == podman ]]; then
    options+=(--userns=keep-id)
fi
options+=(--user "$(id -u):$(id -g)")
if [[ "$label_mode" == disable ]]; then
    options+=(--security-opt label=disable --volume "$data_dir:/data")
else
    options+=(--volume "$data_dir:/data:Z")
fi
exec "$runtime" "${options[@]}" "$image" "$@"
