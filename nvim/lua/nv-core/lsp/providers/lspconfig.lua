local M = {}

function M.on_attach(client, bufnr)
  local function buf_set_option(...) vim.api.nvim_buf_set_option(bufnr, ...) end

  -- Enable completion triggered by <c-x><c-o>
  buf_set_option('omnifunc', 'v:lua.vim.lsp.omnifunc')

  -- So that the only client with format capabilities is efm
  if client.name ~= 'efm' then
    client.resolved_capabilities.document_formatting = false
  end

  require('lsp_signature').on_attach({
    bind = true, -- This is mandatory, otherwise border config won't get registered.
    handler_opts = {
      border = "single"
    }
  }, bufnr)

  -- for some reason, lsp saga highlights have to happen here
  require('nv-core.theme.highlights').lsp()
end

M.capabilities = require('cmp_nvim_lsp').update_capabilities(vim.lsp.protocol.make_client_capabilities())

M.flags = {
  debounce_text_changes = 150,
}

return M
