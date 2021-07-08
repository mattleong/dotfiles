local g = vim.g
local cmd = vim.cmd
local map = require('nv-utils').map

g.floaterm_width = 0.8
g.floaterm_height = 0.8
g.floaterm_title = '|terminal($1/$2)|'
g.floaterm_opener = 'vsplit'

cmd 'hi FloatermBorder guibg=None guifg=#7aa2f7'

map('n', '<C-l>', ':FloatermToggle<CR>', { noremap = true })
map('t', '<C-l>', [[<C-\><C-n>]], { noremap = true })

