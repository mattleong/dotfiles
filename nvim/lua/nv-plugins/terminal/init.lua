local g = vim.g
local colors = require('nv-plugins.theme.colors')
local utils = require('nv-utils')
local highlight = utils.highlight;
local M = {}

function M.init(set_map)
  g.floaterm_width = 0.8
  g.floaterm_height = 0.8
  g.floaterm_title = '|👾 ($1/$2)|'
  g.floaterm_opener = 'vsplit'

  -- todo: move to colors.lua
  highlight('FloatermBorder', 'None', colors.border)

  if set_map == true then
    require('nv-plugins.terminal.mappings').init()
  end
end

return M
