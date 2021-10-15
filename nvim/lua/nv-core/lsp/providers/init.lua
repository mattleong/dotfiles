local default_config = require('nv-core.lsp.providers.lspconfig')
local lua_config = require('nv-core.lsp.providers.lua')
local efm_config = require('nv-core.lsp.providers.efm')
local ts_config = require('nv-core.lsp.providers.tsserver').config
local lspinstall = require('lspinstall');
local lspconfig = require('lspconfig');

local function setup_servers()
  lspinstall.setup()
  local servers = lspinstall.installed_servers()
  for _, server in pairs(servers) do
    if server == 'lua' then
      local config = vim.tbl_deep_extend('force', default_config, lua_config)
      lspconfig[server].setup(config)
    elseif server == 'efm' then
      local config = vim.tbl_deep_extend('force', default_config, efm_config)
      lspconfig[server].setup(config)
    elseif server == 'typescript' then
      local config = vim.tbl_deep_extend('force', default_config, ts_config)
      lspconfig[server].setup(config)
    else
      lspconfig[server].setup(default_config)
    end
  end

  -- Automatically reload after `:LspInstall <server>` so we don't have to restart neovim
  lspinstall.post_install_hook = function ()
    setup_servers() -- reload installed servers
    vim.cmd("bufdo e") -- this triggers the FileType autocmd that starts the server
  end
end

setup_servers()

