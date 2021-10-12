local highlight = require('nv-utils').highlight
local colors = require('nv-plugins.theme.colors')
local M = {}

function M.on_attach(client, bufnr)
  local function buf_set_option(...) vim.api.nvim_buf_set_option(bufnr, ...) end

  -- Enable completion triggered by <c-x><c-o>
  buf_set_option('omnifunc', 'v:lua.vim.lsp.omnifunc')

  -- Mappings.
  require('nv-plugins.lsp.mappings')

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
  -- signature highlight color
  highlight('LspSignatureActiveParameter', 'None', colors.purple)

  -- needs to highlight after lsp start
  highlight('NormalFloat', 'Normal', 'NormalFloat')
  highlight('LspSagaDocTruncateLine', 'NormalFloat', colors.border)
  highlight('LspSagaDiagnosticTruncateLine', 'NormalFloat', colors.border)
  highlight('LspSagaRenamePromptPrefix', 'NormalFloat', colors.teal)
  highlight('LspSagaDiagnosticHeader', 'NormalFloat', colors.blue)
  vim.cmd [[
    highlight clear LspSagaHoverBorder
    highlight clear LspFloatWinBorder
    highlight clear LspSagaRenameBorder
    highlight clear LspSagaSignatureHelpBorder
    highlight clear LspSagaLspFinderBorder
    highlight clear LspSagaCodeActionBorder
    highlight clear LspSagaAutoPreview
    highlight clear LspSagaDefPreviewBorder
    highlight clear LspFloatWinNormal
    highlight clear LspSagaDiagnosticBorder

    highlight link LspFloatWinNormal NormalFloat
    highlight link LspSagaHoverBorder FloatBorder
    highlight link LspFloatWinBorder FloatBorder
    highlight link LspSagaRenameBorder FloatBorder
    highlight link LspSagaSignatureHelpBorder FloatBorder
    highlight link LspSagaLspFinderBorder FloatBorder
    highlight link LspSagaCodeActionBorder FloatBorder
    highlight link LspSagaAutoPreview FloatBorder
    highlight link LspSagaDefPreviewBorder FloatBorder
    highlight link LspSagaDiagnosticBorder FloatBorder
  ]]
end

M.capabilities = require('cmp_nvim_lsp').update_capabilities(vim.lsp.protocol.make_client_capabilities())

M.flags = {
  debounce_text_changes = 150,
}

return M
