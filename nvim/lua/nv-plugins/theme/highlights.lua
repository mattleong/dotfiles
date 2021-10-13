local colors = require('nv-plugins.theme.colors')
local highlight = require('nv-utils').highlight
local M = {}

function M.init()
  -- diagnostic colors
  highlight('DiagnosticVirtualTextError', 'None', colors.error)
  highlight('DiagnosticVirtualTextWarn', 'None', colors.warn)
  highlight('DiagnosticVirtualTextInfo', 'None', colors.info)
  highlight('DiagnosticVirtualTextHint', 'None', colors.teal)
  highlight('LspDiagnosticsSignError', 'None', colors.error)
  highlight('LspDiagnosticsSignWarning', 'None', colors.warn)
  highlight('LspDiagnosticsSignInformation', 'None', colors.info)
  highlight('LspDiagnosticsSignHint', 'None', colors.teal)
  -- highlight('LspSagaBorder', 'None', colors.border)
  highlight('FloatermBorder', 'None', colors.border)

end

function M.lsp()
  -- signature highlight color
  highlight('LspSignatureActiveParameter', 'None', colors.orange)

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

return M
