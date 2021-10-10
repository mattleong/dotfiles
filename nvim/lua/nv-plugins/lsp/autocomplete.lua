local cmp = require('cmp')
local lspkind = require('lspkind')

vim.cmd [[
autocmd FileType TelescopePrompt lua require('cmp').setup.buffer { enabled = false }
]]

cmp.setup({
  snippet = {
    expand = function(args)
      -- For `vsnip` user.
      vim.fn["vsnip#anonymous"](args.body)
    end,
  },
  mapping = {
    ['<C-d>'] = cmp.mapping.scroll_docs(-4),
    ['<C-f>'] = cmp.mapping.scroll_docs(4),
    ['<C-Space>'] = cmp.mapping.complete(),
    ['<C-e>'] = cmp.mapping.close(),
    ['<CR>'] = cmp.mapping.confirm({
      behavior = cmp.ConfirmBehavior.Replace,
      select = true,
    }),
    ['<Tab>'] = function(fallback)
      if cmp.visible() then
        cmp.select_next_item()
      else
        fallback()
      end
    end,
    ['<S-Tab>'] = function(fallback)
      if cmp.visible() then
        cmp.select_prev_item()
      else
        fallback()
      end
    end,
  },
  documentation = {
    border = 'single',
    winhighlight = 'NormalFloat:NormalFloat,FloatBorder:NormalFloat',
  },
  experimental = {
    ghost_text = true,
  },
  sources = {
    { name = 'nvim_lsp' },
    { name = 'buffer' },
    { name = 'vsnip' },
    { name = "nvim_lua" },
    { name = "path" },
    { name = "calc" },
  },
  formatting = {
    format = function(entry, vim_item)
      vim_item.menu = ({
        nvim_lsp = "[LSP]",
        buffer = "[Buf]",
        vsnip = "[Vsnip]",
        nvim_lua = "[Lua]",
      })[entry.source.name]
      return lspkind.cmp_format({with_text = true, maxwidth = 50})(entry, vim_item)
    end,
  }
})

require('nvim-autopairs').setup({
  disable_filetype = { "TelescopePrompt" , "vim" },
})

require("nvim-autopairs.completion.cmp").setup({
  map_cr = true, --  map <CR> on insert mode
  map_complete = true, -- it will auto insert `(` (map_char) after select function or method item
  auto_select = true, -- automatically select the first item
  insert = false, -- use insert confirm behavior instead of replace
  map_char = { -- modifies the function or method delimiter by filetypes
    all = '(',
    tex = '{'
  }
})

require'navigator'.setup({
  code_action = {enable = true, sign = true, sign_priority = 40, virtual_text = false},
  code_lens_action = {enable = true, sign = true, sign_priority = 40, virtual_text = false},
  border = "single",
  lsp = {
    diagnostic_scrollbar_sign = nil, -- experimental:  diagnostic status in scroll bar area; set to nil to disable the diagnostic sign,
    format_on_save = false, -- set to false to disasble lsp code format on save (if you are using prettier/efm/formater etc)
  },
  treesitter_analysis = true, -- treesitter variable context
  default_mapping = false,  -- set to false if you will remap every key

  keymaps = {
    {key = "gd", func = "definition()"}
  }, -- a list of key maps
  lspinstall = true, -- set to true if you would like use the lsp installed by lspinstall
})
