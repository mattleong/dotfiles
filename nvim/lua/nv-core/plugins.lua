local present, packer = pcall(require, "nv-core.packer")

if not present then
   return false
end

local use = packer.use

return packer.startup(function()
  use({
    'wbthomason/packer.nvim',
    run = function()
      require('compiled/packer')
    end,
  })

  use('lewis6991/impatient.nvim')

  use({ -- color scheme
    'folke/tokyonight.nvim',
    config = function()
      vim.g.tokyonight_style = 'night'
      vim.g.tokyonight_sidebars = { 'qf', 'packer' }
      vim.cmd('color tokyonight')
    end,
  })

  use({ -- icons
    'kyazdani42/nvim-web-devicons',
    after = 'tokyonight.nvim',
  })

  -- theme stuff
  use({ -- statusline
    'NTBBloodbath/galaxyline.nvim',
    branch = 'main',
    requires = { 'kyazdani42/nvim-web-devicons', opt = true },
    config = function()
      require('nv-core.statusline')
    end,
    after = 'nvim-web-devicons',
  })

  -- file explorer
  use({
    'kyazdani42/nvim-tree.lua',
    config = function()
      require('nv-core.file-explorer').init()
    end,
    opt = true,
    cmd = {
      'NvimTreeClipboard',
      'NvimTreeClose',
      'NvimTreeFindFile',
      'NvimTreeOpen',
      'NvimTreeRefresh',
      'NvimTreeToggle',
    },
  })

  use({ -- lsp
    'williamboman/nvim-lsp-installer',
    requires = {
      'neovim/nvim-lspconfig',
      'ray-x/lsp_signature.nvim',
      'jose-elias-alvarez/nvim-lsp-ts-utils',
    },
    config = function()
      require('nv-core.lsp')
    end,
  })

  -- autocompletion
  use({
    'hrsh7th/nvim-cmp',
    config = function()
      require('nv-core.lsp.autocomplete')
    end,
    requires = {
      'hrsh7th/cmp-nvim-lsp',
      'hrsh7th/cmp-buffer',
      'hrsh7th/cmp-path',
      'hrsh7th/nvim-cmp',
      'L3MON4D3/LuaSnip',
      'saadparwaiz1/cmp_luasnip',
      'windwp/nvim-autopairs',
      'onsails/lspkind-nvim',
    },
  })

  -- diagnostics
  use({
    'folke/trouble.nvim',
    opt = true,
    config = function()
      require('nv-core.lsp.diagnostics').trouble()
    end,
    cmd = {
      'Trouble',
      'TroubleClose',
      'TroubleToggle',
      'TroubleRefresh',
    },
  })

  -- git
  use({
    'tpope/vim-fugitive',
    opt = true,
    cmd = 'Git',
  })
  use({
    'lewis6991/gitsigns.nvim',
    requires = { 'nvim-lua/plenary.nvim' },
    event = 'BufRead',
    config = function()
      require('gitsigns').setup()
    end,
  })

  use({ -- floating terminal
    'voldikss/vim-floaterm',
    opt = true,
    cmd = { 'FloatermToggle', 'FloatermNew', 'FloatermSend' },
    config = function()
      require('nv-core.terminal').init()
    end,
  })

  use({ -- file navigation
    'nvim-telescope/telescope.nvim',
    requires = {
      'nvim-lua/popup.nvim',
      'nvim-lua/plenary.nvim',
      {
        'nvim-telescope/telescope-fzf-native.nvim',
        run = 'make',
      },
    },
    config = function()
      require('nv-core.file-navigation').init()
    end,
    event = 'BufRead',
  })

  use({ -- session management
    'rmagatti/auto-session',
    event = 'VimEnter',
    config = function()
      require('auto-session').setup({
        pre_save_cmds = { 'NvimTreeClose', 'TroubleClose', 'cclose' },
      })
    end,
  })

  use({ -- lang/syntax stuff
    'nvim-treesitter/nvim-treesitter',
    requires = {
      'windwp/nvim-ts-autotag',
      'JoosepAlviste/nvim-ts-context-commentstring',
      'nvim-treesitter/nvim-treesitter-refactor',
    },
    config = function()
      require('nv-core.treesitter')
    end,
  })

  -- comments and stuff
  use({
    'tpope/vim-commentary',
    event = 'BufRead',
  })

  -- colorized hex codes
  use({
    'norcalli/nvim-colorizer.lua',
    opt = true,
    cmd = { 'ColorizerToggle' },
    config = function()
      require('colorizer').setup()
    end,
  })

  use({
    'AckslD/nvim-whichkey-setup.lua',
    event = 'BufRead',
    requires = { 'liuchengxu/vim-which-key' },
    config = function()
      require('nv-core.theme.whichkey')
    end,
  })
end)