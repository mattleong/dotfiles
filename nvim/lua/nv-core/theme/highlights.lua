local colors = require('nv-core.theme.colors')
local highlight = require('nv-utils').highlight
local M = {}

function M.init()
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

  -- legacy lsp colors
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

  -- needs to highlight after lsp start, why? idk
  vim.cmd([[
    highlight clear NormalFloat
    highlight link NormalFloat Normal
  ]])
  highlight('FloatermBorder', 'None', colors.floatBorder)
end

return M
