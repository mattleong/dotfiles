local g = vim.g
local colors = require('nv-plugins.theme.colors')
local utils = require('nv-utils')
local highlight = utils.highlight;
local M = {}

function M.init()
  g.floaterm_width = 0.8
  g.floaterm_height = 0.8
  g.floaterm_title = '|👾 ($1/$2)|'
  g.floaterm_opener = 'vsplit'

  highlight('FloatermBorder', 'None', colors.border)

  require('nv-plugins.terminal.mappings').init()
end

return M
