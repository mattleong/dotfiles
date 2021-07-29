# Path to your oh-my-zsh installation.
export ZSH=~/.oh-my-zsh

export EDITOR=nvim

export ZSH_CUSTOM=~/.zsh

export DOTS=~/.config/dotfiles

# Set name of the theme to load.
# Look in ~/.oh-my-zsh/themes/
# Optionally, if you set this to "random", it'll load a random theme each
# time that oh-my-zsh is loaded.
ZSH_THEME="powerlevel10k/powerlevel10k"

# Which plugins would you like to load? (plugins can be found in ~/.oh-my-zsh/plugins/*)
# Custom plugins may be added to ~/.oh-my-zsh/custom/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(wd sudo fzf zsh-syntax-highlighting)

#disable update prompt
DISABLE_UPDATE_PROMPT=true
SAVEHIST=10000

# neovim
alias v='nvim'

# User configuration
# Development
# Git shit
gl() {
	if [ $1 ] ; then
		git log --oneline -"$1" ;
	else
		git log --oneline -25
	fi
}

# git
alias gs='git status -sb'
alias ga='git add -A'
alias gd='git diff'
alias gds='git diff --stat'
alias gca='git commit --amend'
alias glg='git log --graph --abbrev-commit --decorate --format=format:"%C(bold blue)%h%C(reset) - %C(bold green)(%ar)%C(reset) %C(white)%s%C(reset) %C(dim white)- %an%C(reset)%C(bold yellow)%d%C(reset)" --all'

gc() {
	git commit -m "$1"
}

if [[ -f "$HOME/.config/dotfiles/zsh/.env" ]]; then
	source ~/.config/dotfiles/zsh/.env
fi

pw() {
	case $1 in
		mj)
			if [[ $2 == "ssh" ]]; then
				ssh -A -p 22 mleong@$MJ_PREPROD_IP
			fi

			if [[ $2 == "storybook" ]]; then
				wd react-components
				nvm use default
				yarn storybook
			fi
		;;
		mg)
			if [[ $2 == "start" ]]; then
				osascript \
					-e 'tell application "iTerm" to activate' \
					-e 'tell application "System Events" to tell process "iTerm" to keystroke "t" using command down' \
					-e 'tell application "System Events" to tell process "iTerm" to keystroke "wd cp; nvm use 12.8; yarn start"' \
					-e 'tell application "System Events" to tell process "iTerm" to key code 52'
			fi

			if [[ $2 == "login" ]]; then
				workon cockpit
				cp_login $MG_CP_LOGIN
			fi
		;;
	esac
}

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

export NVM_DIR="$([ -z "${XDG_CONFIG_HOME-}" ] && printf %s "${HOME}/.nvm" || printf %s "${XDG_CONFIG_HOME}/nvm")"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" # This loads nvm

if [[ -f "/usr/local/bin/virtualenvwrapper.sh" ]]; then
	source '/usr/local/bin/virtualenvwrapper.sh'
fi

export DENO_INSTALL="/home/mrchu001/.deno"
export PATH="/usr/local/bin:/usr/bin:/usr/local/sbin:/bin:/usr/sbin:/sbin:$HOME/.npm-global/bin:/snap/bin/:$HOME/.cargo/bin:/home/mrchu001/.local/bin:$DENO_INSTALL/bin:$HOME/local/nvim/bin"

source $ZSH/oh-my-zsh.sh
