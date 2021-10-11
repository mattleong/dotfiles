local actions = require('telescope.actions')
local map = require('nv-utils').map

local M = {}

local function set_telescope_mapping()
  map('n', '<leader>p', ':lua require("telescope.builtin").find_files()<CR>', { noremap = true })
  map('n', '<leader>f', ':lua require("telescope.builtin").git_files()<CR>', { noremap = true })
  map('n', '<leader>k', ':lua require("telescope.builtin").buffers()<CR>', { noremap = true })
  map('n', '<leader>s', ':lua require("telescope.builtin").live_grep()<CR>', { noremap = true })
  map('n', '<leader>gc', ':lua require("telescope.builtin").git_commits()<CR>', { noremap = true })
  map('n', '<leader>gs', ':lua require("telescope.builtin").git_status()<CR>', { noremap = true })
end

local normal_file_mappings = {
  n = {
    ['Q'] =  actions.smart_add_to_qflist + actions.open_qflist,
    ['q'] =  actions.smart_send_to_qflist + actions.open_qflist,
    ['<tab>'] = actions.toggle_selection + actions.move_selection_next,
    ['<s-tab>'] = actions.toggle_selection +  actions.move_selection_previous,
    ['v'] =  actions.file_vsplit,
    ['s'] =  actions.file_split,
    ['<cr>'] = actions.file_edit,
  }
};

function M.init()
  -- highlights
  require('telescope').setup {
    defaults = {
      prompt_prefix = '🔍 ',
      selection_caret = ' ',
      file_ignore_patterns = {
        ".git/"
      },
      vimgrep_arguments = {
        'rg',
        '--ignore',
        '--hidden',
        '--color=never',
        '--no-heading',
        '--with-filename',
        '--line-number',
        '--column',
        '--smart-case'
      },
    },
    extensions = {
      fzf = {
        fuzzy = true,                    -- false will only do exact matching
        override_generic_sorter = true,  -- override the generic sorter
        override_file_sorter = true,     -- override the file sorter
        case_mode = "smart_case",        -- or "ignore_case" or "respect_case"
        -- the default case_mode is "smart_case"
      }
    },
    pickers ={
      buffers = {
        prompt_title = '✨ Search Buffers ✨',
        mappings = {
          n = {
            ['d'] = actions.delete_buffer
          },
        },
        sort_mru = true,
      },
      find_files = {
        prompt_title = '✨ Search Project ✨',
        mappings = normal_file_mappings,
        hidden = true,
      },
      git_files = {
        prompt_title = '✨ Search Git Project ✨',
        mappings = normal_file_mappings,
        hidden = true,
      },
      live_grep = {
        prompt_title = '✨ Live Grep ✨',
        mappings = normal_file_mappings,
      },
    },
  }

  require('telescope').load_extension('fzf')

  set_telescope_mapping()
end

return M
