local defaults = require('nv-plugins.lsp.defaults')
local lua_defaults = require('nv-plugins.lsp.lua')
-- vim.cmd("autocmd CursorHold,CursorHoldI * lua vim.lsp.diagnostic.show_line_diagnostics({border="..vim.inspect(borders)..", focusable=false})")

local function setup_servers()
  require'lspinstall'.setup()
  local servers = require'lspinstall'.installed_servers()
  for _, server in pairs(servers) do
    if server == 'lua' then
      require'lspconfig'[server].setup(lua_defaults)
    else
      require'lspconfig'[server].setup(defaults)
    end
  end
end

setup_servers()

-- Automatically reload after `:LspInstall <server>` so we don't have to restart neovim
require'lspinstall'.post_install_hook = function ()
  setup_servers() -- reload installed servers
  vim.cmd("bufdo e") -- this triggers the FileType autocmd that starts the server
end

require('nv-plugins.lsp.diagnostics')
require('nv-plugins.lsp.autocomplete')
