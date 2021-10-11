local icons = require('nv-plugins.theme.icons')

local signs = {
  Error = icons.error .. ' ',
  Warning = icons.warn .. ' ',
  Hint = icons.hint .. ' ',
  Information = icons.info .. ' ',
}

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

local t = vim.fn.sign_getdefined('DiagnosticSignWarn')
if vim.tbl_isempty(t) then
  for type, icon in pairs(signs) do
    local hl = 'DiagnosticSign' .. type
    vim.fn.sign_define(hl, { text = icon, texthl = hl, numhl = '' })
  end
end
