local util = require('lspconfig').util

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
  root_dir = function(fname)
    return util.root_pattern("tsconfig.base.json")(fname) or
    util.root_pattern(".git")(fname) or
    util.root_pattern("package.json")(fname) or
    util.root_pattern(".eslintrc.js")(fname) or
    util.root_pattern("tsconfig.json")(fname);
  end,
  filetypes = vim.tbl_keys(filetypes),
  settings = {
    rootMarkers = {".eslintrc.js", ".git/"},
    languages = filetypes,
  }
}

