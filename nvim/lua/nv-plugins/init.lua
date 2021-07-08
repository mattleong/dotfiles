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
			cmd = { 'FloatermToggle', 'FloatermNew', 'FloatermSend' },
		}

		use {
			'nvim-telescope/telescope.nvim',
			config = function()
				require('telescope').setup {
					defaults = {
						prompt_prefix = '🔍',
					},
					extensions = {
						fzy_native = {
							override_generic_sorter = false,
							override_file_sorter = true,
						},
					},
				}

				require('telescope').load_extension('fzy_native')
			end,
			requires = {
				{'nvim-lua/popup.nvim'},
				{'nvim-lua/plenary.nvim'},
				{'nvim-telescope/telescope-fzy-native.nvim'}
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
			requires = {'rmagatti/auto-session', 'nvim-telescope/telescope.nvim'},
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

require 'nv-plugins.nv-telescope'
require 'nv-plugins.nv-treesitter'
require 'nv-plugins.nv-nvimtree'
require 'nv-plugins.nv-galaxyline'
require 'nv-plugins.nv-floaterm'
cmd 'source ~/.config/nvim/lua/nv-plugins/coc.vim'
