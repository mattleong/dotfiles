#!/usr/bin/env bash
set -euo pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="stow"
DRY_RUN=false
VERBOSE=false
ADOPT=false

usage() {
  cat <<'EOF'
Usage: ./install-dotfiles.sh [options]

Options:
  -n, --dry-run   Show what would change without creating links
  -D, --delete    Remove managed links
  -a, --adopt     Import existing target files into stow packages (not Rex)
  -v, --verbose   Print extra stow output
  -h, --help      Show this help

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

run_rex_link() {
  local source="$DOTFILES_DIR/rex"
  local target="$HOME/.config/rex"
  local managed=false

  [[ -d "$source" ]] || return
  if [[ -L "$target" ]] && [[ "$(cd "$target" 2>/dev/null && pwd -P)" == "$(cd "$source" && pwd -P)" ]]; then
    managed=true
  fi

  if [[ "$MODE" == "delete" ]]; then
    if [[ "$managed" == "true" ]]; then
      echo "[delete] $target"
      [[ "$DRY_RUN" == "true" ]] || rm "$target"
    fi
  elif [[ "$managed" == "true" ]]; then
    echo "[stow] $target already links to $source"
  elif [[ -e "$target" || -L "$target" ]]; then
    echo "Error: $target already exists; move it aside before linking Rex." >&2
    return 1
  else
    echo "[stow] $target -> $source"
    if [[ "$DRY_RUN" != "true" ]]; then
      mkdir -p "$(dirname "$target")"
      ln -s "$source" "$target"
    fi
  fi
}

run_stow_group "$HOME" zsh aerospace
run_rex_link

echo "Done."
