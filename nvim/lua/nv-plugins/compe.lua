local map = require('nv-utils').map
local t = function(str)
	return vim.api.nvim_replace_termcodes(str, true, true, true)
end

local check_back_space = function()
	local col = vim.fn.col('.') - 1
	return col == 0 or vim.fn.getline('.'):sub(col, col):match('%s') ~= nil
end

-- Use (s-)tab to:
--- move to prev/next item in completion menuone
--- jump to prev/next snippet's placeholder
_G.tab_complete = function()
	if vim.fn.pumvisible() == 1 then
		return t "<C-n>"
	elseif vim.fn['vsnip#available'](1) == 1 then
		return t "<Plug>(vsnip-expand-or-jump)"
	elseif check_back_space() then
		return t "<Tab>"
	else
		return vim.fn['compe#complete']()
	end
end

_G.s_tab_complete = function()
	if vim.fn.pumvisible() == 1 then
		return t "<C-p>"
	elseif vim.fn['vsnip#jumpable'](-1) == 1 then
		return t "<Plug>(vsnip-jump-prev)"
	else
		-- If <S-Tab> is not working in your terminal, change it to <C-h>
		return t "<S-Tab>"
	end
end

-- _G.confirm_pump = function()
-- 	return
-- end

map('i', '<Tab>', 'v:lua.tab_complete()', { expr = true })
map('s', '<Tab>', 'v:lua.tab_complete()', { expr = true })
map('i', '<S-Tab>', 'v:lua.s_tab_complete()', { expr = true })
map('s', '<S-Tab>', 'v:lua.s_tab_complete()', { expr = true })
map('i', '<CR>', "compe#confirm('<CR>')", { expr = true })

require'compe'.setup {
	source = {
		path = true,
		nvim_lsp = true,
		nvim_lua = true,
		buffer = false,
		calc = false,
		vsnip = false,
		ultisnips = false,
		luasnip = false,
		treesitter = false,
	},
}
