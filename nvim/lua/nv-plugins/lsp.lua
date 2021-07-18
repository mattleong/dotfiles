local nvim_lsp = require('lspconfig')
local map = require('nv-utils').map

-- Use an on_attach function to only map the following keys
-- after the language server attaches to the current buffer
local on_attach = function(client, bufnr)
	-- See `:help vim.lsp.*` for documentation on any of the below functions
	map('n', 'gD', '<Cmd>lua vim.lsp.buf.declaration()<CR>')
	map('n', 'gd', '<Cmd>lua vim.lsp.buf.definition()<CR>')
	map('n', 'K', '<Cmd>lua vim.lsp.buf.hover()<CR>')
	map('n', 'gi', '<cmd>lua vim.lsp.buf.implementation()<CR>')
	map('n', '<C-k>', '<cmd>lua vim.lsp.buf.signature_help()<CR>')
	map('n', '<space>wa', '<cmd>lua vim.lsp.buf.add_workspace_folder()<CR>')
	map('n', '<space>wr', '<cmd>lua vim.lsp.buf.remove_workspace_folder()<CR>')
	map('n', '<space>wl', '<cmd>lua print(vim.inspect(vim.lsp.buf.list_workspace_folders()))<CR>')
	map('n', '<space>D', '<cmd>lua vim.lsp.buf.type_definition()<CR>')
	map('n', '<space>rn', '<cmd>lua vim.lsp.buf.rename()<CR>')
	map('n', '<space>ca', '<cmd>lua vim.lsp.buf.code_action()<CR>')
	map('n', 'gr', '<cmd>lua vim.lsp.buf.references()<CR>')
	map('n', '<space>e', '<cmd>lua vim.lsp.diagnostic.show_line_diagnostics()<CR>')
	map('n', '[d', '<cmd>lua vim.lsp.diagnostic.goto_prev()<CR>')
	map('n', ']d', '<cmd>lua vim.lsp.diagnostic.goto_next()<CR>')
	map('n', '<space>q', '<cmd>lua vim.lsp.diagnostic.set_loclist()<CR>')
	map("n", "<space>f", "<cmd>lua vim.lsp.buf.formatting()<CR>")
end

-- Use a loop to conveniently call 'setup' on multiple servers and
-- map buffer local keybindings when the language server attaches
local servers = { "tsserver" }
for _, lsp in ipairs(servers) do
	nvim_lsp[lsp].setup {
		on_attach = on_attach,
		flags = {
			debounce_text_changes = 150,
		}
	}
end
