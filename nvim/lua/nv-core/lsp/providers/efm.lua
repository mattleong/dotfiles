local lspconfig = require('lspconfig')

local prettier = { formatCommand = 'prettier --stdin-filepath ${INPUT}', formatStdin = true }
local filetypes = {
  css = { prettier },
  html = { prettier },
  javascript = { prettier },
  javascriptreact = { prettier },
  json = { prettier },
  markdown = { prettier },
  scss = { prettier },
  typescript = { prettier },
  typescriptreact = { prettier },
  yaml = { prettier },
}

return {
  init_options = { documentFormatting = true, codeAction = true },
  root_dir = lspconfig.util.root_pattern { '.git/', '.' },
  filetypes = vim.tbl_keys(filetypes),
  settings = { languages = filetypes },
}
