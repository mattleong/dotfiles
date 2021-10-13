local colors = require('nv-plugins.theme.colors')
local highlight = require('nv-utils').highlight

require('nv-plugins.lsp.providers')
require('nv-plugins.lsp.diagnostics')
require('nv-plugins.lsp.autocomplete')
require('nv-plugins.lsp.ide')

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
