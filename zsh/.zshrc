# Path to your oh-my-zsh installation.
export ZSH=~/.oh-my-zsh

export ZSH_CUSTOM=~/.zsh

# Set name of the theme to load.
# Look in ~/.oh-my-zsh/themes/
ZSH_THEME="powerlevel10k/powerlevel10k"

# Which plugins would you like to load? (plugins can be found in ~/.oh-my-zsh/plugins/*)
# Custom plugins may be added to ~/.oh-my-zsh/custom/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(wd zsh-syntax-highlighting vi-mode)

#disable update prompt
DISABLE_UPDATE_PROMPT=true
SAVEHIST=10000

# end zsh settings
source $ZSH/oh-my-zsh.sh

# set editor to neovim
export EDITOR=nvim

# source secrets
if [[ -f "$HOME/.config/dotfiles/zsh/.env" ]]; then
	source ~/.config/dotfiles/zsh/.env
fi

# source aliases
if [[ -f "$HOME/.config/dotfiles/zsh/.aliases" ]]; then
	source ~/.config/dotfiles/zsh/.aliases
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
  local nvmrc_path
  nvmrc_path="$(nvm_find_nvmrc)"

  if [ -n "$nvmrc_path" ]; then
    local nvmrc_node_version
    nvmrc_node_version=$(nvm version "$(cat "${nvmrc_path}")")

    if [ "$nvmrc_node_version" = "N/A" ]; then
      nvm install
    elif [ "$nvmrc_node_version" != "$(nvm version)" ]; then
      nvm use
    fi
  elif [ -n "$(PWD=$OLDPWD nvm_find_nvmrc)" ] && [ "$(nvm version)" != "$(nvm version default)" ]; then
    echo "Reverting to nvm default version"
    nvm use default
  fi
}

add-zsh-hook chpwd load-nvmrc

export PATH="/usr/local/bin:/usr/bin:/usr/local/sbin:/bin:/usr/sbin:/sbin:$HOME/.npm-global/bin:$HOME/.cargo/bin:$HOME/local/nvim/bin:$GOPATH"

eval "$(pyenv init -)"
eval "$(pyenv init --path)"
eval "$(pyenv virtualenv-init -)"
