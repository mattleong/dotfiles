local map = require('nv-utils').map
local M = {}

function M.init()
  map('n', '<C-n>', ':NvimTreeToggle<CR>', { noremap = true })
  map('n', '<leader>r', ':NvimTreeRefresh<CR>', { noremap = true })
end

return M
