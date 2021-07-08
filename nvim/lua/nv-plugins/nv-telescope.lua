local map = require('nv-utils').map

map('n', '<C-j>', ':Telescope find_files<CR>', { noremap = true })
map('n', '<C-k>', ':Telescope buffers<CR>', { noremap = true })
map('n', '<C-s>', ':Telescope live_grep<CR>', { noremap = true })
map('n', '<leader>fs', ':SearchSession<CR>', { noremap = true })
