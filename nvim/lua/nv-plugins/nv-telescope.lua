local map = require('nv-utils').map
local M = {}

local function init_mappings()
	map('n', '<leader>f', ':lua require("telescope.builtin").find_files()<CR>', { noremap = true })
	map('n', '<leader>k', ':lua require("telescope.builtin").buffers()<CR>', { noremap = true })
	map('n', '<leader>s', ':lua require("telescope.builtin").live_grep()<CR>', { noremap = true })
	map('n', '<leader>q', ':lua require("telescope.builtin").quickfix()<CR>', { noremap = true })
	map('n', '<leader>gd', ':lua require("telescope.builtin").git_commits()<CR>', { noremap = true })
	map('n', '<leader>gs', ':lua require("telescope.builtin").git_status()<CR>', { noremap = true })
	map('n', '<leader>x', ':SearchSession<CR>', { noremap = true })
end

function M.init()
	require('telescope').setup {
		defaults = {
			prompt_prefix = '🔍 ',
			selection_caret = '➜ ',
		},
		pickers ={
			buffers = {
				prompt_title = 'Search Buffers',
			},
			find_files = {
				prompt_title = 'Search Project',
				find_command = {"rg", "--hidden", "--ignore", "--files", "--smart-case"},
			}
		},
		extensions = {
			fzy_native = {
				override_generic_sorter = false,
				override_file_sorter = true,
			},
		},
	}

	require('telescope').load_extension('fzy_native')
	init_mappings()
end

return M
