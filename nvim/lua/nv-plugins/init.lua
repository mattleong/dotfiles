local cmd = vim.cmd
local fn = vim.fn

-- Automatically install packer.nvim
local install_path = fn.stdpath('data') .. '/site/pack/packer/start/packer.nvim'
if fn.empty(fn.glob(install_path)) > 0 then
	cmd('!git clone https://github.com/wbthomason/packer.nvim ' .. install_path)
end

local packer = require 'packer'
local use = packer.use

packer.startup(
	function()
		use 'wbthomason/packer.nvim'

		use 'kyazdani42/nvim-web-devicons'
		use 'kyazdani42/nvim-tree.lua'

		-- lang/syntax stuff
		use {
			'nvim-treesitter/nvim-treesitter',
			run = ':TSUpdate',
			requires = {
				'windwp/nvim-ts-autotag',
				'JoosepAlviste/nvim-ts-context-commentstring',
			},
		}

		-- theme stuff
		use {
			'glepnir/galaxyline.nvim',
			branch = 'main',
			requires = {
				'kyazdani42/nvim-web-devicons',
				opt = true
			},
		}

		use {
			'folke/tokyonight.nvim',
			config = function()
				vim.g.tokyonight_style = 'night'
				vim.g.tokyonight_sidebars = { "qf", "packer" }
				vim.cmd 'color tokyonight'
			end,
		}

		-- floating terminal
		use {
			'voldikss/vim-floaterm',
			opt = true,
			cmd = { 'FloatermToggle', 'FloatermNew', 'FloatermSend', },
		}

		use {
			'nvim-telescope/telescope.nvim',
			requires = {
				'nvim-lua/popup.nvim',
				'nvim-lua/plenary.nvim',
			},
		}

		-- git
		use 'tpope/vim-fugitive'
		use 'tpope/vim-rhubarb'
		use {
			'lewis6991/gitsigns.nvim',
			requires = { 'nvim-lua/plenary.nvim' },
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
		use {
			'rmagatti/session-lens',
			requires = {
				'rmagatti/auto-session',
				'nvim-telescope/telescope.nvim'
			},
			opt = true,
			cmd = { 'SearchSession' },
			config = function()
				require('session-lens').setup {
					shorten_path = true,
				}
			end
		}

		-- autocomplete/ide
		use { 'neoclide/coc.nvim', branch = 'release', }

		-- colorized hex codes
		use {
			'norcalli/nvim-colorizer.lua',
			opt = true,
			cmd = { 'ColorizerToggle', },
			config = function()
				require'colorizer'.setup()
			end
		}

	end
)

require 'nv-plugins.telescope'.init()
require 'nv-plugins.treesitter'.init()
require 'nv-plugins.nvimtree'
require 'nv-plugins.galaxyline'
require 'nv-plugins.floaterm'
cmd 'source ~/.config/nvim/lua/nv-plugins/coc.vim'
