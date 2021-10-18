local g = vim.g
local M = {}

function M.init()
  g.floaterm_width = 0.8
  g.floaterm_height = 0.8
  g.floaterm_title = '|👾 ($1/$2)|'
  g.floaterm_opener = 'vsplit'
end

return M
