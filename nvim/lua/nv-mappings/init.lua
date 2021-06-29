require("nv-mappings.nv-floatterm")
require("nv-mappings.nv-nvimtree")

local map = require('nv-utils').map
local g = vim.g

g.mapleader = ' '

-- editor
map('n', '<C-s>', ':Rg<space>', { noremap = true })
map('n', '<C-k>', ':Buffers<CR>', { noremap = true })
map('n', '<C-j>', ':GFiles<CR>', { noremap = true })
