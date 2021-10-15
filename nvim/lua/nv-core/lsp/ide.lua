vim.diagnostic.config({
  underline = true,
  update_in_insert = false,
  virtual_text = {
    spacing = 4,
    source = 'always',
    severity = 'error'
    --    prefix = '👾',
  },
  signs = true,
  severity_sort = true,
})

-- lsp settings
vim.lsp.handlers["textDocument/signatureHelp"] =
  vim.lsp.with(
    vim.lsp.handlers.signature_help,
    { border = "single" }
  )
vim.lsp.handlers["textDocument/hover"] =
  vim.lsp.with(
    vim.lsp.handlers.hover,
    {
      border = "single",
    }
)
