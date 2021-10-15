local cmd = vim.cmd
local fn = vim.fn

-- Automatically install packer.nvim
local install_path = fn.stdpath('data') .. '/site/pack/packer/start/packer.nvim'
if fn.empty(fn.glob(install_path)) > 0 then
  cmd('!git clone https://github.com/wbthomason/packer.nvim ' .. install_path)
end

local packer = require 'packer'
local use = packer.use

packer.startup({
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
        require 'nv-core.statusline'
      end,
      after = 'nvim-web-devicons'
    }

    -- file explorer
    use {
      'kyazdani42/nvim-tree.lua',
      config = function()
        require 'nv-core.file-explorer'.init()
      end,
      cmd = {
        "NvimTreeClipboard",
        "NvimTreeClose",
        "NvimTreeFindFile",
        "NvimTreeOpen",
        "NvimTreeRefresh",
        "NvimTreeToggle",
      },
    }

    -- git
    use {
      'tpope/vim-fugitive',
      cmd = 'Git'
    }
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
        require('nv-core.terminal').init()
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
        require 'nv-core.file-navigation'.init()
      end,
      event = 'BufRead'
    }

    use { -- session management
      'rmagatti/auto-session',
      event = 'VimEnter',
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
        require('nv-core.lsp.treesitter')
      end,
      -- event = 'BufEnter'
    }

    -- comments and stuff
    use {
      'tpope/vim-commentary',
      event = "BufRead",
    }

    use { -- lsp
      'neovim/nvim-lspconfig',
      requires = {
        'kabouzeid/nvim-lspinstall',
      },
      config = function()
        require 'nv-core.lsp'
      end,
    }

    use { -- signature help
      "ray-x/lsp_signature.nvim",
      after = 'nvim-lspconfig'
    }

    -- code actions, diagnostics
    use {
      'tami5/lspsaga.nvim',
      after = 'nvim-lspconfig',
      config = function()
        require('nv-core.lsp.ide')
      end,
    }

    -- autocompletion
    use {
      'hrsh7th/nvim-cmp',
      after = 'nvim-lspconfig',
      config = function()
        require('nv-core.lsp.autocomplete')
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
      config = function()
        require('nv-core.lsp.diagnostics').trouble()
      end,
      cmd = {
        'Trouble',
        'TroubleClose',
        'TroubleToggle',
        'TroubleRefresh'
      }
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
      event = "BufWinEnter",
      requires = {'liuchengxu/vim-which-key'},
      config = function()
        require 'nv-core.whichkey'
      end
    }
  end,
  config = {
    display = {
      open_fn = function()
        return require('packer.util').float({ border = 'single' })
      end,
      prompt_border = 'single'
    },
    auto_clean = true,
    compile_on_sync = true,
  }
})
