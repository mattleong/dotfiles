local actions = require('telescope.actions')
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
				mappings = {
					n = {
						['d'] = actions.delete_buffer
					}
				}
			},
			find_files = {
				prompt_title = 'Search Project',
				find_command = {'rg', '--hidden', '--ignore', '--files', '--smart-case'},
				mappings = {
					n = {
						['Q'] =  actions.smart_add_to_qflist + actions.open_qflist,
						['q'] =  actions.smart_send_to_qflist + actions.open_qflist,
						['<tab>'] = actions.toggle_selection + actions.move_selection_next,
						['<s-tab>'] = actions.move_selection_previous,
						['v'] =  actions.file_vsplit,
						['s'] =  actions.file_split,
						['<cr>'] = actions.file_edit,
					}
				}
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
