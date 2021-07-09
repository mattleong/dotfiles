local g = vim.g
local cmd = vim.cmd
local map = require('nv-utils').map

g.floaterm_width = 0.8
g.floaterm_height = 0.8
g.floaterm_title = '|🖥️($1/$2)|'
g.floaterm_opener = 'vsplit'

cmd 'hi FloatermBorder guibg=None guifg=#3d59a1'

map('n', '<C-l>', ':FloatermToggle<CR>', { noremap = true })
map('t', '<C-l>', [[<C-\><C-n>]], { noremap = true })

