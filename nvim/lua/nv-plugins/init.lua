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
    use {
      'wbthomason/packer.nvim',
--      event = 'VimEnter'
    }

    use { -- color scheme
      'folke/tokyonight.nvim',
      config = function()
        vim.g.tokyonight_style = 'night'
        vim.g.tokyonight_sidebars = { "qf", "packer" }
        vim.cmd 'color tokyonight'
      end,
    }

    use { -- icons
      'kyazdani42/nvim-web-devicons',
      after = 'tokyonight.nvim'
    }
    --
    -- theme stuff
    use { -- statusline
      'NTBBloodbath/galaxyline.nvim',
      branch = 'main',
      requires = { "kyazdani42/nvim-web-devicons", opt = true },
      config = function()
        require 'nv-plugins.statusline'
      end,
      after = 'nvim-web-devicons'
    }

    -- file explorer
    use {
      'kyazdani42/nvim-tree.lua',
      config = function()
        require 'nv-plugins.file-explorer'.init()
      end,
    }

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


    use { -- floating terminal
      'voldikss/vim-floaterm',
      opt = true,
      cmd = { 'FloatermToggle', 'FloatermNew', 'FloatermSend', },
      config = function()
        require('nv-plugins.terminal').init()
      end
    }

    use { -- file navigation
      'nvim-telescope/telescope.nvim',
      requires = {
        'nvim-lua/popup.nvim',
        'nvim-lua/plenary.nvim',
        {
          'nvim-telescope/telescope-fzf-native.nvim',
          run = 'make'
        }
      },
      config = function()
        require 'nv-plugins.file-navigation'.init()
      end,
      event = "BufRead",
    }

    use { -- session management
      'rmagatti/auto-session',
      config = function()
        require('auto-session').setup {
          pre_save_cmds = { 'NvimTreeClose', 'TroubleClose', 'cclose' },
        }
      end,
    }

    use { -- lang/syntax stuff
      'nvim-treesitter/nvim-treesitter',
      run = ':TSUpdate',
      requires = {
        'windwp/nvim-ts-autotag',
        'JoosepAlviste/nvim-ts-context-commentstring',
        'nvim-treesitter/nvim-treesitter-refactor',
      },
      config = function()
        -- todo: doesn't belong in lsp
        require('nv-plugins.lsp.treesitter')
      end,
      -- event = 'BufEnter'
    }

    -- comments and stuff
    use {
      'tpope/vim-commentary',
      event = "InsertEnter",
    }

    use { -- lsp
      'neovim/nvim-lspconfig',
      requires = {
        'kabouzeid/nvim-lspinstall',
      },
      config = function()
        -- todo: doesn't belong in lsp
        require 'nv-plugins.lsp'
      end,
    }

    use { -- signature help
      "ray-x/lsp_signature.nvim",
      after = 'nvim-lspconfig'
      -- event = "InsertEnter",
    }

    -- code actions, diagnostics
    use {
      'tami5/lspsaga.nvim',
      after = 'nvim-lspconfig',
      -- event = "BufRead",
      config = function()
        require('nv-plugins.lsp.ide')
      end,
    }

    -- autocompletion
    use {
      'hrsh7th/nvim-cmp',
      after = 'nvim-lspconfig',
      --    event = "InsertEnter",
      config = function()
        require('nv-plugins.lsp.autocomplete')
      end,
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

    -- diagnostics
    use {
      'folke/trouble.nvim',
      event = "BufRead",
      config = function()
        local icons = require('nv-plugins.theme.icons')
        local signs = {
          Error = icons.error,
          Warning = icons.warn,
          Hint = icons.hint,
          Information = icons.info,
        }

        require("trouble").setup {
          mode = "lsp_document_diagnostics", -- "lsp_workspace_diagnostics", "lsp_document_diagnostics", "quickfix", "lsp_references", "loclist"
          signs = {
            error = signs.error,
            warn = signs.warn,
            info = signs.info,
            hint = signs.hint
          }
        }
      end
    }

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
      event = "BufRead",
      requires = {'liuchengxu/vim-which-key'},
      config = function()
        require 'nv-plugins.whichkey'
      end
    }
  end
)

-- todo: move elsewhere...
require('nv-plugins.terminal.mappings').init()
