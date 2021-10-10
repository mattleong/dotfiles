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

    -- git
    use 'tpope/vim-fugitive'
    use 'tpope/vim-rhubarb'
    use {
      'lewis6991/gitsigns.nvim',
      requires = { 'nvim-lua/plenary.nvim' },
      event = "BufRead",
      config = function()
        require('gitsigns').setup()
      end
    }

    -- theme stuff
    use {
      'NTBBloodbath/galaxyline.nvim',
      branch = 'main',
      requires = { "kyazdani42/nvim-web-devicons", opt = true }
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
    use {'nvim-telescope/telescope-fzf-native.nvim', run = 'make' }


    -- session management
    use {
      'rmagatti/auto-session',
      config = function()
        require('auto-session').setup {
          pre_save_cmds = { 'NvimTreeClose' },
        }
      end,
    }

    -- lang/syntax stuff
    -- use 'ChristianChiarulli/vim-solidity'
    use {
      'nvim-treesitter/nvim-treesitter',
      run = ':TSUpdate',
      requires = {
        'windwp/nvim-ts-autotag',
        'JoosepAlviste/nvim-ts-context-commentstring',
        'nvim-treesitter/nvim-treesitter-refactor',
      },
    }

    -- autocomplete/ide
    use {
      'neovim/nvim-lspconfig',
    }
    use {
      'kabouzeid/nvim-lspinstall',
    }

    use {
      'hrsh7th/nvim-cmp',
      requires = {
        'hrsh7th/cmp-nvim-lsp',
        'hrsh7th/cmp-buffer',
        'hrsh7th/cmp-path',
        'hrsh7th/nvim-cmp',
        'hrsh7th/cmp-vsnip',
        'hrsh7th/vim-vsnip',
        'windwp/nvim-autopairs',
        'onsails/lspkind-nvim',
      },
    }

    -- use {
    --   'neoclide/coc.nvim',
    --   event = "BufWinEnter",
    --   branch = 'release',
    -- }

    -- colorized hex codes
    use {
      'norcalli/nvim-colorizer.lua',
      opt = true,
      cmd = { 'ColorizerToggle', },
      config = function()
        require'colorizer'.setup()
      end
    }

     use {
      'AckslD/nvim-whichkey-setup.lua',
      requires = {'liuchengxu/vim-which-key'},
    }
  end
)

require 'nv-plugins.nvimtree'.init()
require 'nv-plugins.telescope'.init()
require 'nv-plugins.treesitter'.init()
require 'nv-plugins.galaxyline'
require 'nv-plugins.floaterm'
require 'nv-plugins.lsp'
require 'nv-plugins.whichkey'
-- cmd 'source ~/.config/nvim/lua/nv-plugins/coc.vim'
