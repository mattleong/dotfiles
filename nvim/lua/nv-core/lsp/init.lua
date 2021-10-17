require('nv-core.lsp.providers')
require('nv-core.lsp.diagnostics').init()
-- require('nv-core.lsp.autocomplete')
require('nv-core.lsp.ide')

vim.lsp.handlers['textDocument/signatureHelp'] = vim.lsp.with(vim.lsp.handlers.signature_help, { border = 'single' })
vim.lsp.handlers['textDocument/hover'] = vim.lsp.with(vim.lsp.handlers.hover, {
  border = 'single',
})
