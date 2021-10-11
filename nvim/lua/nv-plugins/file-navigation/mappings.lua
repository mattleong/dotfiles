local actions = require('telescope.actions')
local map = require('nv-utils').map
local M = {}

function M.init()
  map('n', '<leader>p', ':lua require("telescope.builtin").find_files()<CR>', { noremap = true })
  map('n', '<leader>f', ':lua require("telescope.builtin").git_files()<CR>', { noremap = true })
  map('n', '<leader>k', ':lua require("telescope.builtin").buffers()<CR>', { noremap = true })
  map('n', '<leader>s', ':lua require("telescope.builtin").live_grep()<CR>', { noremap = true })
  map('n', '<leader>gc', ':lua require("telescope.builtin").git_commits()<CR>', { noremap = true })
  map('n', '<leader>gs', ':lua require("telescope.builtin").git_status()<CR>', { noremap = true })
end

M.normal = {
  n = {
    ['Q'] =  actions.smart_add_to_qflist + actions.open_qflist,
    ['q'] =  actions.smart_send_to_qflist + actions.open_qflist,
    ['<tab>'] = actions.toggle_selection + actions.move_selection_next,
    ['<s-tab>'] = actions.toggle_selection +  actions.move_selection_previous,
    ['v'] =  actions.file_vsplit,
    ['s'] =  actions.file_split,
    ['<cr>'] = actions.file_edit,
  }
};

return M
