local g = vim.g
local map = require('nv-utils').map

g.mapleader = ' '

-- Quickfix
map('n', '<leader>ck', ':cexpr []<CR>', { noremap = true })
map('n', '<leader>cc', ':cclose <CR>', { noremap = true })
map('n', '<leader>co', ':copen <CR>', { noremap = true })
map('n', '<leader>cf', ':cfdo %s/', { noremap = true })

require('nv-core.lsp.mappings')
require('nv-core.file-explorer.mappings').init()
require('nv-core.file-navigation.mappings').init()
require('nv-core.terminal.mappings').init()
