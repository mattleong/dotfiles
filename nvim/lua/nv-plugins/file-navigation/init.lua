local actions = require('telescope.actions')
local mappings = require('nv-plugins.file-navigation.mappings')

local M = {}

function M.init()
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
        mappings = mappings.normal,
        hidden = true,
      },
      git_files = {
        prompt_title = '✨ Search Git Project ✨',
        mappings = mappings.normal,
        hidden = true,
      },
      live_grep = {
        prompt_title = '✨ Live Grep ✨',
        mappings = mappings.normal,
      },
    },
  }

  require('telescope').load_extension('fzf')

  mappings.init()
end

return M
