local map = require('nv-utils').map

-- editor
map('n', '<C-s>', ':Rg<space>', { noremap = true })
map('n', '<C-k>', ':Buffers<CR>', { noremap = true })
map('n', '<C-j>', ':GFiles<CR>', { noremap = true })
