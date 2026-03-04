# Path to your oh-my-zsh installation.
export ZSH=~/.oh-my-zsh

export ZSH_CUSTOM=~/.zsh

# Which plugins would you like to load? (plugins can be found in ~/.oh-my-zsh/plugins/*)
# Custom plugins may be added to ~/.oh-my-zsh/custom/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(zsh-syntax-highlighting zsh-autosuggestions wd vi-mode nvm gh sudo uv)

zstyle ':omz:plugins:nvm' autoload yes
# zstyle ':omz:plugins:nvm' lazy yes

#disable update prompt
DISABLE_UPDATE_PROMPT=true
SAVEHIST=10000

# end zsh settings
source $ZSH/oh-my-zsh.sh

# set editor to neovim
export EDITOR=nvim

# source secrets
if [[ -f "$HOME/.env" ]]; then
	source ~/.env
fi

# source aliases
if [[ -f "$HOME/.aliases" ]]; then
	source ~/.aliases
fi

export PATH="$(go env GOPATH)/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/usr/local/sbin:/bin:/usr/sbin:/sbin:$HOME/.npm-global/bin:$HOME/.cargo/bin:$HOME/local/nvim/bin:$HOME/.local/bin"

export PYENV_ROOT="$HOME/.pyenv"
if [[ -d "$PYENV_ROOT/bin" ]]; then
	export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
fi

if command -v pyenv >/dev/null 2>&1; then
	_pyenv_lazy_init() {
		unset -f pyenv _pyenv_lazy_init
		eval "$(command pyenv init - zsh)"
	}

	pyenv() {
		_pyenv_lazy_init
		command pyenv "$@"
	}
fi

eval "$(starship init zsh)"
eval "$(codex completion zsh)"

if command -v wt >/dev/null 2>&1; then eval "$(command wt config shell init zsh)"; fi
