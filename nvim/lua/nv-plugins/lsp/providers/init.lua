local defaults = require('nv-plugins.lsp.providers.lspconfig')
local lua_config = require('nv-plugins.lsp.providers.lua')
local efm_config = require('nv-plugins.lsp.providers.efm')
local lspinstall = require('lspinstall');
local lspconfig = require('lspconfig');

local function setup_servers()
  lspinstall.setup()
  local servers = lspinstall.installed_servers()
  for _, server in pairs(servers) do
    if server == 'lua' then
      lspconfig[server].setup(lua_config)
    elseif server == 'efm' then
      lspconfig[server].setup(efm_config)
    else
      lspconfig[server].setup(defaults)
    end
  end

  -- Automatically reload after `:LspInstall <server>` so we don't have to restart neovim
  lspinstall.post_install_hook = function ()
    setup_servers() -- reload installed servers
    vim.cmd("bufdo e") -- this triggers the FileType autocmd that starts the server
  end
end

setup_servers()

