local g = vim.g
local cmd = vim.cmd
local map = require('nv-utils').map
local colors = require('nv-plugins.colors')
local utils = require('nv-utils')
local highlight = utils.highlight;

g.floaterm_width = 0.8
g.floaterm_height = 0.8
g.floaterm_title = '|👾 ($1/$2)|'
g.floaterm_opener = 'vsplit'

highlight('FloatermBorder', 'None', colors.border)

map('n', '<C-l>', ':FloatermToggle<CR>', { noremap = true })
map('t', '<C-l>', [[<C-\><C-n>]], { noremap = true })

