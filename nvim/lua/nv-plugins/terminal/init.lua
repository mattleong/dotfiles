local g = vim.g
local M = {}

function M.init(set_map)
  g.floaterm_width = 0.8
  g.floaterm_height = 0.8
  g.floaterm_title = '|👾 ($1/$2)|'
  g.floaterm_opener = 'vsplit'

  if set_map == true then
    require('nv-plugins.terminal.mappings').init()
  end
end

return M
