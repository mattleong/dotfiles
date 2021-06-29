local map = require('nv-utils').map

map('n', '<C-l>', ':FloatermToggle<CR>', { noremap = true })
map('t', '<C-l>', [[<C-\><C-n>]], { noremap = true })

vim.g.nvim_tree_disable_netrw = 0
