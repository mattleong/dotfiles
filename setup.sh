# install brew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# install apps and tools
brew install stow \
  zsh \
  starship \
  neovim \
  ripgrep \
  font-fira-code \
  go \
  eza \
  tmux \
  gh \
  codex \
  anomalyco/tap/opencode \
  --cask obsidian \
  --cask ghostty \
  --cask claude-code \
  --cask keepingyouawake \
  --cask bitwarden \
  --cask yaak \
  --cask raycast \
  --cask gimp

# install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash

# install oh-my-zsh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting
git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions
