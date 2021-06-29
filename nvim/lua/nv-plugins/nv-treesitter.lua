require"nvim-treesitter.configs".setup {
	ensure_installed = {
		"typescript",
		"javascript",
		"tsx",
		"html",
		"css",
		"bash",
		"lua",
		"json",
		"python",
		"php",
		"scss"
	},
	highlight = {
		enable = true,
		use_languagetree = true,
	},
	indent = { enable = true },
	autotag = {
		enable = true,
	},
	context_commentstring = {
		enable = true,
	}
};
