local themeColors = require('tokyonight.colors').setup()

local colors = {
  white = themeColors.fg_dark,
  bg = themeColors.bg,
  bg_highlight = themeColors.bg_highlight,
  blue = themeColors.blue,
  purple = themeColors.magenta,
  orange = themeColors.orange,
  teal = themeColors.teal,
  red = themeColors.red,
  diffAdd = themeColors.git.add,
  diffModified = themeColors.git.change,
  diffDeleted = themeColors.git.delete,
  info = themeColors.green2,
  error = themeColors.magenta2,
  warn = themeColors.orange,
  floatBorder = themeColors.border_highlight,
}

return colors
