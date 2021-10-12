local map = require('nv-utils').map

-- Mappings.
local opts = { noremap = true, silent = true }

-- See `:help vim.lsp.*` for documentation on any of the below functions
map('n', 'gd', '<cmd>lua vim.lsp.buf.definition()<CR>', opts)
map('n', 'gc', '<cmd>lua vim.lsp.buf.declaration()<CR>', opts)
map('n', 'gi', '<cmd>lua vim.lsp.buf.implementation()<CR>', opts)
map('n', 'gt', '<cmd>lua vim.lsp.buf.type_definition()<CR>', opts)
map('n', 'gn', '<cmd>lua require("lspsaga.rename").rename()<CR>', opts)
map('n', 'gr', '<cmd>lua vim.lsp.buf.references()<CR>', opts)
map('n', '<space>gd', '<cmd>lua require"lspsaga.provider".preview_definition()<CR>', opts)
map('n', '<space>gh', '<cmd>lua require("lspsaga.provider").lsp_finder()<CR>', opts)
map('n', '<space>ga', '<cmd>lua require("lspsaga.codeaction").code_action()<CR>', opts)
map('v', '<space>ga', ':<C-U>lua require("lspsaga.codeaction").range_code_action()<CR>', opts)
map('n', '<space>ge', '<cmd>lua require("lspsaga.diagnostic").show_line_diagnostics()<CR>', opts)
map('n', '<space>gf', '<cmd>lua vim.lsp.buf.formatting()<CR>', opts)
map('n', '<C-k>', '<cmd>lua require("lspsaga.signaturehelp").signature_help()<CR>', opts)
map('n', 'K', '<cmd>lua require("lspsaga.hover").render_hover_doc()<CR>', opts)
map('n', '[g', '<cmd>lua require("lspsaga.diagnostic").lsp_jump_diagnostic_prev()<CR>', opts)
map('n', ']g', '<cmd>lua require("lspsaga.diagnostic").lsp_jump_diagnostic_next()<CR>', opts)
map('n', '<space>wa', '<cmd>lua vim.lsp.buf.add_workspace_folder()<CR>', opts)
map('n', '<space>wr', '<cmd>lua vim.lsp.buf.remove_workspace_folder()<CR>', opts)
map('n', '<space>wl', '<cmd>lua print(vim.inspect(vim.lsp.buf.list_workspace_folders()))<CR>', opts)
