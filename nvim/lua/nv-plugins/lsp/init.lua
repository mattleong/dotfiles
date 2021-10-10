local defaults = require('nv-plugins.lsp.defaults')
local lua_defaults = require('nv-plugins.lsp.lua')
local colors = require('nv-plugins.colors')
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


vim.cmd(string.format('hi DiagnosticVirtualTextError guibg=None guifg=%s', colors.error))
vim.cmd(string.format('hi DiagnosticVirtualTextWarn guibg=None guifg=%s', colors.warn))
vim.cmd(string.format('hi DiagnosticVirtualTextInfo guibg=None guifg=%s', colors.info))
vim.cmd(string.format('hi DiagnosticVirtualTextHint guibg=None guifg=%s', colors.teal))

-- lsp settings
vim.lsp.handlers["textDocument/signatureHelp"] =
  vim.lsp.with(
    vim.lsp.handlers.signature_help,
    { border = "single" }
  )
vim.lsp.handlers["textDocument/hover"] =
  vim.lsp.with(
    vim.lsp.handlers.hover,
    { border = "single" }
  )

vim.lsp.handlers["textDocument/publishDiagnostics"] = vim.lsp.with(
  vim.lsp.diagnostic.on_publish_diagnostics, {
    underline = false,
    update_in_insert = false,
    virtual_text = true
  }
)

require('nv-plugins.lsp.diagnostics')
require('nv-plugins.lsp.autocomplete')
