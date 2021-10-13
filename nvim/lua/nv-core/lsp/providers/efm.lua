local lspconfig = require('lspconfig')

local eslint = {
  lintCommand = 'eslint_d -f unix --stdin --stdin-filename ${INPUT}',
  lintIgnoreExitCode = true,
  lintStdin = true,
  lintFormats = { '%f:%l:%c: %m' },
  formatCommand = 'eslint_d --fix-to-stdout --stdin --stdin-filename=${INPUT}',
  formatStdin = true,
}
local prettier = { formatCommand = 'prettier --stdin-filepath ${INPUT}', formatStdin = true }
local filetypes = {
  css = { prettier },
  html = { prettier },
  javascript = { prettier, eslint },
  javascriptreact = { prettier, eslint },
  json = { prettier },
  markdown = { prettier },
  scss = { prettier },
  typescript = { prettier, eslint },
  typescriptreact = { prettier, eslint },
  yaml = { prettier },
}

return {
  init_options = { documentFormatting = true, codeAction = true },
  root_dir = lspconfig.util.root_pattern { '.git/', '.' },
  filetypes = vim.tbl_keys(filetypes),
  settings = { languages = filetypes },
}

