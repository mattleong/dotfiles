local icons = require('nv-core.theme.icons')
local M = {}

local signs = {
  Error = icons.error .. ' ',
  Warning = icons.warn .. ' ',
  Warn = icons.warn .. ' ',
  Hint = icons.hint .. ' ',
  Information = icons.info .. ' ',
}

function M.init()
  vim.diagnostic.config({
    underline = true,
    update_in_insert = false,
    virtual_text = {
      spacing = 4,
      source = 'always',
      -- severity = 'error'
      -- prefix = '👾',
    },
    signs = true,
    severity_sort = true,
  })

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
end

function M.trouble()
  require('trouble').setup({
    mode = 'lsp_document_diagnostics', -- "lsp_workspace_diagnostics", "lsp_document_diagnostics", "quickfix", "lsp_references", "loclist"
    signs = {
      error = signs.error,
      warn = signs.warn,
      info = signs.info,
      hint = signs.hint,
    },
  })
end

return M
