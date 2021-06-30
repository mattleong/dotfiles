local g = vim.g
local cmd = vim.cmd
local map = require('nv-utils').map

g.floaterm_width = 0.8
g.floaterm_height = 0.8
g.floaterm_rootmarkers = {'.project', '.git', '.hg', '.svn', '.root', '.gitignore'}

cmd 'hi FloatermBorder guibg=None'

map('n', '<C-l>', ':FloatermToggle<CR>', { noremap = true })
map('t', '<C-l>', [[<C-\><C-n>]], { noremap = true })

