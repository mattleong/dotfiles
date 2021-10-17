local map = require('nv-utils').map
local wk = require('whichkey_setup')

map('n', '<leader>', ':WhichKey "<Space>"<CR>', { silent = true })

require('whichkey_setup').config({
  hide_statusline = false,
  default_keymap_settings = {
    silent = true,
    noremap = true,
  },
  default_mode = 'n',
})

local keymap = {
  w = { ':w!<CR>', 'save file' }, -- set a single command and text
  f = 'find git files',
  p = 'find project files',
  k = 'find buffers',
  s = 'search project',
  af = 'code action for qf',
  r = 'refresh tree',
  c = {
    name = '+quickfix',
    k = 'clear quickfix list',
    c = 'close quickfix list',
    o = 'open quickfix list',
    f = 'search & replace quickfix list',
  },
  g = {
    name = '+code',
    c = 'git commits',
    s = 'git status',
    a = 'code action',
  },
  h = {
    name = '+gitsigns',
  },
}

wk.register_keymap('leader', keymap)
