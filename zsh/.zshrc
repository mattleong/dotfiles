# Path to your oh-my-zsh installation.
export ZSH=~/.oh-my-zsh

export EDITOR=nvim

export ZSH_CUSTOM=~/.zsh

# Set name of the theme to load.
# Look in ~/.oh-my-zsh/themes/
ZSH_THEME="powerlevel10k/powerlevel10k"

# Which plugins would you like to load? (plugins can be found in ~/.oh-my-zsh/plugins/*)
# Custom plugins may be added to ~/.oh-my-zsh/custom/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(wd sudo fzf zsh-syntax-highlighting vi-mode)

#disable update prompt
DISABLE_UPDATE_PROMPT=true
SAVEHIST=10000

# end zsh settings
source $ZSH/oh-my-zsh.sh

# neovim
rebuild-nvim() {
  brew unlink neovim
  brew install --build-from-source --HEAD --force --fetch-HEAD neovim
}

alias v='nvim'

# Git shit
gl() {
	if [ $1 ] ; then
		git log --oneline -"$1" ;
	else
		git log --oneline -25
	fi
}

alias gs='git status -sb'
alias ga='git add -A'
alias gd='git diff'
alias gds='git diff --stat'
alias gca='git commit --amend'
alias gcan='git commit --amend --no-edit'
alias glg='git log --graph --abbrev-commit --decorate --format=format:"%C(bold blue)%h%C(reset) - %C(bold green)(%ar)%C(reset) %C(white)%s%C(reset) %C(dim white)- %an%C(reset)%C(bold yellow)%d%C(reset)" --all'

pwgit() {
  RETURN=$(pwd)
  DEST="$(wd path pwapps)/$2"

  wd portal
  cd .git

  case $1 in
    add)
      git worktree add -B "$2" "$DEST"
      cd "$DEST"
      yarn
      ;;

    *)
      git worktree "$@"
      cd $RETURN
      ;;
  esac
}

gc() {
	git commit -m "$1"
}

# source secrets
if [[ -f "$HOME/.config/dotfiles/zsh/.env" ]]; then
	source ~/.config/dotfiles/zsh/.env
fi

# mcd: Makes new directory and jumps into it
mcd () { mkdir -p "$1" && cd "$1"; }

alias cd..='cd ../'                         # Go back 1 directory
# level (for fast typers)
alias ..='cd ../'                           # Go back 1 directory level
alias ...='cd ../../'                       # Go back 2 directory levels
alias .3='cd ../../../'                     # Go back 3 directory levels
alias .4='cd ../../../../'                  # Go back 4 directory levels
alias .5='cd ../../../../../'               # Go back 5 directory levels
alias .6='cd ../../../../../../'            # Go back 6 directory levels
alias c='clear'                             # c:            Clear

if command -v exa &>/dev/null; then
	alias ls='exa'
fi

# COSMICNIVM
export COSMICNVIM_INSTALL_DIR=~/dev/cosmic/CosmicNvim
export COSMICNVIM_CONFIG_DIR=~/dev/cosmic/cosmic-config

# Set nvm dir
export NVM_DIR="$([ -z "${XDG_CONFIG_HOME-}" ] && printf %s "${HOME}/.nvm" || printf %s "${XDG_CONFIG_HOME}/nvm")"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" # This loads nvm

# place this after nvm initialization!
autoload -U add-zsh-hook
load-nvmrc() {
  local node_version="$(nvm version)"
  local nvmrc_path="$(nvm_find_nvmrc)"

  if [ -n "$nvmrc_path" ]; then
    local nvmrc_node_version=$(nvm version "$(cat "${nvmrc_path}")")

    if [ "$nvmrc_node_version" = "N/A" ]; then
      nvm install
    elif [ "$nvmrc_node_version" != "$node_version" ]; then
      nvm use
    fi
  elif [ "$node_version" != "$(nvm version default)" ]; then
    echo "Reverting to nvm default version"
    nvm use default
  fi
}
add-zsh-hook chpwd load-nvmrc

export DENO_INSTALL="/home/mrchu001/.deno"
export PATH="/usr/local/bin:/usr/bin:/usr/local/sbin:/bin:/usr/sbin:/sbin:$HOME/.npm-global/bin:/snap/bin/:$HOME/.cargo/bin:/home/mrchu001/.local/bin:$DENO_INSTALL/bin:$HOME/local/nvim/bin:$GOPATH"

eval "$(pyenv init -)"
eval "$(pyenv init --path)"
eval "$(pyenv virtualenv-init -)"
