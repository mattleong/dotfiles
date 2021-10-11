local colors = require('nv-plugins.theme.colors')
local highlight = require('nv-utils').highlight
local map = require('nv-utils').map
local M = {}

function M.on_attach(client, bufnr)
  local function buf_set_option(...) vim.api.nvim_buf_set_option(bufnr, ...) end

  -- Enable completion triggered by <c-x><c-o>
  buf_set_option('omnifunc', 'v:lua.vim.lsp.omnifunc')

  -- Mappings.
  local opts = { noremap = true, silent = true }
  local popup_opts = '{ border = "double", focusable = false, }'
  local win_opts = '{ popup_opts = ' .. popup_opts .. '}'

  -- See `:help vim.lsp.*` for documentation on any of the below functions
  map('n', 'gd', '<cmd>lua vim.lsp.buf.definition()<CR>', opts)
  map('n', 'gD', '<cmd>lua vim.lsp.buf.declaration()<CR>', opts)
  map('n', 'gi', '<cmd>lua vim.lsp.buf.implementation()<CR>', opts)
  map('n', 'gt', '<cmd>lua vim.lsp.buf.type_definition()<CR>', opts)
  map('n', 'gn', '<cmd>lua vim.lsp.buf.rename()<CR>', opts)
  map('n', 'gr', '<cmd>lua vim.lsp.buf.references()<CR>', opts)
  map('n', '<space>ga', '<cmd>lua vim.lsp.buf.code_action()<CR>', opts)
  map('n', '<space>ge', '<cmd>lua vim.lsp.diagnostic.show_line_diagnostics()<CR>', opts)
  map('n', '<C-k>', '<cmd>lua vim.lsp.buf.signature_help()<CR>', opts)
  map('n', 'K', '<cmd>lua vim.lsp.buf.hover()<CR>', opts)
  map('n', '[g', '<cmd>lua vim.lsp.diagnostic.goto_prev('.. win_opts ..')<CR>', opts)
  map('n', ']g', '<cmd>lua vim.lsp.diagnostic.goto_next('.. win_opts ..')<CR>', opts)
  map('n', '<space>wa', '<cmd>lua vim.lsp.buf.add_workspace_folder()<CR>', opts)
  map('n', '<space>wr', '<cmd>lua vim.lsp.buf.remove_workspace_folder()<CR>', opts)
  map('n', '<space>wl', '<cmd>lua print(vim.inspect(vim.lsp.buf.list_workspace_folders()))<CR>', opts)
  map('n', '<space>q', '<cmd>lua vim.lsp.diagnostic.set_loclist()<CR>', opts)

  if client.resolved_capabilities.document_formatting then
    map('n', '<space>gf', '<cmd>lua vim.lsp.buf.formatting()<CR>', opts)
  end

  require('lsp_signature').on_attach({
    bind = true, -- This is mandatory, otherwise border config won't get registered.
    handler_opts = {
      border = "single"
    }
  }, bufnr)
  -- signature highlight color
  highlight('LspSignatureActiveParameter', 'None', colors.purple)

  -- needs to highlight after lsp start
  highlight('NormalFloat', 'Normal', 'NormalFloat')
end

M.capabilities = require('cmp_nvim_lsp').update_capabilities(vim.lsp.protocol.make_client_capabilities())

M.flags = {
  debounce_text_changes = 150,
}

return M
