local cmd = vim.cmd
local fn = vim.fn

-- Automatically install packer.nvim
local install_path = fn.stdpath('data') .. '/site/pack/packer/start/packer.nvim'
if fn.empty(fn.glob(install_path)) > 0 then
	cmd('!git clone https://github.com/wbthomason/packer.nvim ' .. install_path)
end

local packer = require('packer')
local use = packer.use

cmd [[packadd packer.nvim]]

packer.startup(
	function()
		use 'wbthomason/packer.nvim'

		use 'kyazdani42/nvim-web-devicons' -- for file icons
		use 'kyazdani42/nvim-tree.lua'

		-- lang stuff
		use {
			'nvim-treesitter/nvim-treesitter',
			run = ':TSUpdate',
			requires = {
				-- Automatically end & rename tags
				'windwp/nvim-ts-autotag',
				-- Dynamically set commentstring based on cursor location in file
				'JoosepAlviste/nvim-ts-context-commentstring',
			}
		}

		-- theme stuff
		use {
		  'glepnir/galaxyline.nvim',
			branch = 'main',
			config = function()
				require 'nv-plugins.nv-galaxyline'
			end,
			requires = { 'kyazdani42/nvim-web-devicons', opt = true },
		}

		use {
			'wadackel/vim-dogrun',
			config = function()
				vim.cmd 'color dogrun'
				vim.cmd 'highlight Normal guibg=none'
			end,
		}

		-- floating terminal
		use {
			'voldikss/vim-floaterm',
			opt = true,
			cmd = { 'FloatermToggle', 'FloatermNew', 'FloatermSend' },
			config = function()
				vim.cmd 'hi FloatermBorder guibg=None'
			end,
		}

		-- file management
		use {
			'junegunn/fzf',
			run = function()
				vim.fn['fzf#install']()
			end
		}
		use 'junegunn/fzf.vim'

		-- git
		use 'tpope/vim-fugitive'
		use 'tpope/vim-rhubarb'
		use {
			'lewis6991/gitsigns.nvim',
			requires = {
				'nvim-lua/plenary.nvim'
			},
			config = function()
				require('gitsigns').setup()
			end
		}

		-- session management
		use {
			'rmagatti/auto-session',
			config = function()
				require('auto-session').setup {
					pre_save_cmds = { 'NvimTreeClose' },
				}
			end,
		}

		-- autocomplete/ide
		use { 'neoclide/coc.nvim', branch = 'release' }

		-- colorized hex codes
		use {
			'norcalli/nvim-colorizer.lua',
			config = function()
				require'colorizer'.setup()
			end
		}

	end
)

require('nv-plugins.nv-fzf')
require('nv-plugins.nv-treesitter')
require('nv-plugins.nv-floaterm')
require("nv-plugins.nv-nvimtree")
cmd 'source ~/.config/nvim/lua/nv-plugins/coc.vim'
