local icons = require('nv-plugins.theme.icons')

local signs = {
  Error = icons.error .. ' ',
  Warning = icons.warn .. ' ',
  Hint = icons.hint .. ' ',
  Information = icons.info .. ' ',
}

if vim.diagnostic ~= nil then
  local t = vim.fn.sign_getdefined('DiagnosticSignWarn')
  if vim.tbl_isempty(t) then
    for type, icon in pairs(signs) do
      local hl = 'DiagnosticSign' .. type
      vim.fn.sign_define(hl, { text = icon, texthl = hl, numhl = '' })
    end
  end
else
  local t = vim.fn.sign_getdefined('LspDiagnosticsSignWarn')
  if vim.tbl_isempty(t) then
    for type, icon in pairs(signs) do
      local hl = 'LspDiagnosticsSign' .. type
      vim.fn.sign_define(hl, { text = icon, texthl = hl, numhl = '' })
    end
  end
end

vim.lsp.handlers['textDocument/publishDiagnostics'] = vim.lsp.with(
  vim.lsp.diagnostic.on_publish_diagnostics, {
    underline = false,
    update_in_insert = false,
    virtual_text = {
      spacing = 4,
      source = 'always',
--      prefix = '👾',
    },
    signs = true,
    severity_sort = true,
  }
)

require('trouble').setup {}
