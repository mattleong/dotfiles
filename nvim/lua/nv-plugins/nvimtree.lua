local g = vim.g
local map = require('nv-utils').map
local M = {}

map('n', '<C-n>', ':NvimTreeToggle<CR>', { noremap = true })
map('n', '<leader>r', ':NvimTreeRefresh<CR>', { noremap = true })

local args = {
  auto_close = true,
  lsp_diagnostics = true,
  hijack_cursor = true,
  update_focused_file = {
    -- enables the feature
    enable      = true,
    -- list of buffer names / filetypes that will not update the cwd if the file isn't found under the current root directory
    -- only relevant when `update_focused_file.update_cwd` is true and `update_focused_file.enable` is true
    ignore_list = {}
  },
}

g.nvim_tree_show_icons = {
	git = 1,
	folders = 1,
	files = 1,
	folder_arrows = 1,
}

g.nvim_tree_icons = {
	default = '',
	symlink = '',
	git = {
		unstaged = "✗",
		staged = "✓",
		unmerged = "",
		renamed = "➜",
		untracked = "★",
		deleted = "",
		ignored = "◌"
	},
	folder = {
		arrow_open = "",
		arrow_closed = "",
		default = "",
		open = "",
		empty = "",
		empty_open = "",
		symlink = "",
		symlink_open = "",
	},
	lsp = {
		hint = "",
		info = "",
		warning = "",
		error = "",
	}
}

function M.init()
  require('nvim-tree').setup(args)
end

return M
