local defaults = require('nv-core.lsp.providers.lspconfig')
local M = {}

function M.on_attach(client, bufnr)
  defaults.on_attach(client, bufnr)
end

return M
