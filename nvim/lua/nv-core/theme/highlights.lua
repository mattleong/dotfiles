local colors = require('nv-core.theme.colors')
local highlight = require('nv-utils').highlight
local M = {}

function M.init()
  -- highlight('LspSagaBorder', 'None', colors.border)
  highlight('FloatermBorder', 'None', colors.floatBorder)
end

function M.lsp()
  -- diagnostic colors
  -- sign colors
  highlight('LspDiagnosticsSignError', 'None', colors.error)
  highlight('LspDiagnosticsSignWarning', 'None', colors.warn)
  highlight('LspDiagnosticsSignInformation', 'None', colors.info)
  highlight('LspDiagnosticsSignHint', 'None', colors.hint)

  --highlight('DiagnosticUnderlineError', 'None', colors.error)
  --highlight('DiagnosticUnderlineWarning', 'None', colors.warn)
  --highlight('DiagnosticUnderlineInformation', 'None', colors.info)
  --highlight('DiagnosticUnderlineHint', 'None', colors.hint)

  -- error colors
  highlight('LspDiagnosticsError', 'None', colors.error)
  highlight('LspDiagnosticsWarn', 'None', colors.warn)
  highlight('LspDiagnosticsInfo', 'None', colors.info)
  highlight('LspDiagnosticsHint', 'None', colors.hint)
  highlight('DiagnosticError', 'None', colors.error)
  highlight('DiagnosticWarn', 'None', colors.warn)
  highlight('DiagnosticInfo', 'None', colors.info)
  highlight('DiagnosticHint', 'None', colors.hint)

  -- signature highlight color
  highlight('LspSignatureActiveParameter', 'None', colors.orange)

  -- needs to highlight after lsp start
  highlight('LspSagaDocTruncateLine', 'NormalFloat', colors.floatBorder)
  highlight('LspSagaDiagnosticTruncateLine', 'NormalFloat', colors.floatBorder)
  highlight('LspSagaCodeActionTruncateLine', 'NormalFloat', colors.floatBorder)
  highlight('LspSagaRenamePromptPrefix', 'NormalFloat', colors.hint)
  highlight('LspSagaDiagnosticHeader', 'NormalFloat', colors.blue)
  vim.cmd [[
    highlight clear NormalFloat
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
    highlight link NormalFloat Normal
  ]]
end

return M
