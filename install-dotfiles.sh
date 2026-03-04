#!/usr/bin/env bash
set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="stow"
DRY_RUN=false
VERBOSE=false
ADOPT=false

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Options:
  -n, --dry-run   Show what would change without creating links
  -D, --delete    Remove links managed by stow
  -a, --adopt     Import existing target files into the stow package
  -v, --verbose   Print extra stow output
  -h, --help      Show this help

Environment overrides:
  CLAUDE_DIR      (default: $HOME/.claude)
  CODEX_DIR       (default: $HOME/.codex)
  OPENCODE_DIR    (default: $HOME/.config/opencode)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--dry-run)
      DRY_RUN=true
      ;;
    -D|--delete)
      MODE="delete"
      ;;
    -a|--adopt)
      ADOPT=true
      ;;
    -v|--verbose)
      VERBOSE=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
  shift
done

if ! command -v stow >/dev/null 2>&1; then
  echo "Error: GNU Stow is not installed." >&2
  echo "Install with Homebrew: brew install stow" >&2
  exit 1
fi

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
CODEX_DIR="${CODEX_DIR:-$HOME/.codex}"
OPENCODE_DIR="${OPENCODE_DIR:-$HOME/.config/opencode}"

run_stow_group() {
  local target="$1"
  shift
  local packages=("$@")

  local existing=()
  for pkg in "${packages[@]}"; do
    if [[ -d "$DOTFILES_DIR/$pkg" ]]; then
      existing+=("$pkg")
    fi
  done

  if [[ ${#existing[@]} -eq 0 ]]; then
    return
  fi

  mkdir -p "$target"
  echo "[$MODE] target: $target packages: ${existing[*]}"

  local args=(
    "--dir" "$DOTFILES_DIR"
    "--target" "$target"
  )

  if [[ "$MODE" == "stow" ]]; then
    args+=("--restow")
  else
    args+=("--delete")
  fi

  if [[ "$MODE" == "stow" && "$ADOPT" == "true" ]]; then
    args+=("--adopt")
  fi

  if [[ "$DRY_RUN" == "true" ]]; then
    args+=("--no")
  fi

  if [[ "$VERBOSE" == "true" ]]; then
    args+=("--verbose")
  fi

  args+=("${existing[@]}")
  stow "${args[@]}"
}

run_stow_group "$HOME" zsh tmux aerospace
run_stow_group "$CLAUDE_DIR" claude
run_stow_group "$CODEX_DIR" codex
run_stow_group "$OPENCODE_DIR" opencode

echo "Done."
