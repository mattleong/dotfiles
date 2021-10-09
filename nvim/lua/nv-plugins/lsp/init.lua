local nvim_lsp = require('lspconfig')
local defaults = require('nv-plugins.lsp.defaults')

-- Setup lspconfig.
local servers = { 'pyright', 'rust_analyzer', 'tsserver' }
for _, lsp in ipairs(servers) do
  nvim_lsp[lsp].setup(defaults)
end

require('nv-plugins.lsp.lua')
require('nv-plugins.lsp.autocomplete')
