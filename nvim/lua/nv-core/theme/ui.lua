-- thank you, nuxshed
local icons = require('nv-core.theme.icons')
local M = {}

local api = vim.api
local lsp = vim.lsp
local buf, win

function M.rename()
  buf, win = api.nvim_create_buf(false, true)

  api.nvim_buf_set_option(buf, 'bufhidden', 'wipe')

  local opts = {
    style = 'minimal',
    border = 'single',
    relative = 'cursor',
    width = 25,
    height = 1,
    row = 1,
    col = 1,
  }

  api.nvim_open_win(buf, true, opts)
  api.nvim_win_set_option(win, 'scrolloff', 0)
  api.nvim_win_set_option(win, 'sidescrolloff', 0)
  api.nvim_buf_set_option(buf, 'modifiable', true)
  api.nvim_buf_set_option(buf, 'buftype', 'prompt')
  -- todo: set hightlight for icon
  vim.fn.prompt_setprompt(buf, ' ' .. icons.folder.arrow_closed .. ' ')
  vim.api.nvim_command('startinsert!')
  local map_opts = { noremap = true }
  api.nvim_buf_set_keymap(buf, 'i', '<esc>', '<CMD>stopinsert <BAR> q!<CR>', map_opts)
  api.nvim_buf_set_keymap(
    buf,
    'i',
    '<CR>',
    "<CMD>stopinsert <BAR> lua require('nv-core.theme.ui')._rename()<CR>",
    map_opts
  )

  function M._rename()
    local newName = vim.trim(vim.fn.getline('.'):sub(5, -1))
    vim.cmd([[q!]])
    local params = lsp.util.make_position_params()
    local currName = vim.fn.expand('<cword>')
    if not (newName and #newName > 0) or newName == currName then
      return
    end
    params.newName = newName
    lsp.buf_request(0, 'textDocument/rename', params)
  end
end

return M
