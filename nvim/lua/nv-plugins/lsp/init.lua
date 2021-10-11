local colors = require('nv-plugins.theme.colors')
local highlight = require('nv-utils').highlight

require('nv-plugins.lsp.providers')
require('nv-plugins.lsp.treesitter')
require('nv-plugins.lsp.diagnostics')
require('nv-plugins.lsp.autocomplete')

-- lsp settings
vim.lsp.handlers["textDocument/signatureHelp"] =
  vim.lsp.with(
    vim.lsp.handlers.signature_help,
    { border = "single" }
  )
vim.lsp.handlers["textDocument/hover"] =
  vim.lsp.with(
    vim.lsp.handlers.hover,
    { border = "single" }
  )

-- diagnostic colors
highlight('DiagnosticVirtualTextError', 'None', colors.error)
highlight('DiagnosticVirtualTextWarn', 'None', colors.warn)
highlight('DiagnosticVirtualTextInfo', 'None', colors.info)
highlight('DiagnosticVirtualTextHint', 'None', colors.teal)
