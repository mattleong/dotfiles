local map = require('nv-utils').map

map('n', '<leader>f', ':lua require("telescope.builtin").find_files()<CR>', { noremap = true })
map('n', '<leader>k', ':lua require("telescope.builtin").buffers()<CR>', { noremap = true })
map('n', '<leader>s', ':lua require("telescope.builtin").live_grep()<CR>', { noremap = true })
map('n', '<leader>gd', ':lua require("telescope.builtin").git_commits()<CR>', { noremap = true })
map('n', '<leader>gs', ':lua require("telescope.builtin").git_status()<CR>', { noremap = true })
map('n', '<leader>x', ':SearchSession<CR>', { noremap = true })
