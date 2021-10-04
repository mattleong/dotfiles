local g = vim.g
local cmd = vim.cmd
local map = require('nv-utils').map
local colors = require('nv-plugins.colors')

g.floaterm_width = 0.8
g.floaterm_height = 0.8
g.floaterm_title = '|👾 ($1/$2)|'
g.floaterm_opener = 'vsplit'

cmd(string.format('hi FloatermBorder guibg=None guifg=%s', colors.darkBlue))

map('n', '<C-l>', ':FloatermToggle<CR>', { noremap = true })
map('t', '<C-l>', [[<C-\><C-n>]], { noremap = true })

