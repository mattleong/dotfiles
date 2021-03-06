local galaxy = require('galaxyline');
local gls = galaxy.section
local diag = require('galaxyline.provider_diagnostic')
local condition = require 'galaxyline.condition'
local fileinfo = require('galaxyline.provider_fileinfo')
local utils = require('nv-utils');
local highlight = utils.highlight;

local colors = {
	white = '#9ea3c0',
	bg = '#1f2335',
	bg_highlight = '#292e42',
	blue = '#7aa2f7',
	purple = '#9d7cd8',
	orange = '#bb7a61',
	teal = '#1abc9c',
	red = '#db4b4b',
	diffAdd = '#266d6a',
	diffModified = '#bb7a61',
	diffDeleted = '#914c54',
	info = '#5d8fac',
	error = '#db4b4b',
	warn = '#ff9e64',
}

local icons = {
	rounded_left_filled = '',
	rounded_right_filled = '',
	arrow_left_filled = '', -- e0b2
	arrow_right_filled = '', -- e0b0
	arrow_left = '', -- e0b3
	arrow_right = '', -- e0b1
	ghost = '',
	warn = '',
	info = '',
	error = '',
	branch = '',
	file = '',
}

local get_mode = function()
	local mode_colors = {
		[110] = { 'NORMAL', colors.blue, colors.bg_highlight },
		[105] = { 'INSERT', colors.teal, colors.bg_highlight },
		[99] = { 'COMMAND', colors.orange, colors.bg_highlight },
		[116] = { 'TERMINAL', colors.blue, colors.bg_highlight },
		[118] = { 'VISUAL', colors.purple, colors.bg_highlight, },
		[22] = { 'V-BLOCK', colors.purple, colors.bg_highlight, },
		[86] = { 'V-LINE', colors.purple, colors.bg_highlight, },
		[82] = { 'REPLACE', colors.red, colors.bg_highlight, },
		[115] = { 'SELECT', colors.red, colors.bg_highlight, },
		[83] = { 'S-LINE', colors.red, colors.bg_highlight, },
	}

	local mode_data = mode_colors[vim.fn.mode():byte()]
	if mode_data ~= nil then
		return mode_data
	end
end

local check_width_and_git_and_buffer = function()
	return condition.hide_in_width() and condition.check_git_workspace() and condition.buffer_not_empty()
end

local check_git_and_width = function()
	return condition.buffer_not_empty() and condition.hide_in_width()
end

local check_buffer_and_width = function()
	return condition.buffer_not_empty() and condition.hide_in_width()
end

local FilePathShortProvider = function()
	local fp = vim.fn.fnamemodify(vim.fn.expand '%', ':~:.:h')
	local tbl = utils.split(fp, '/')
	local len = #tbl

	if len > 2 and tbl[1] ~= '~' then
		return '…/' .. table.concat(tbl, '/', len - 1) .. '/'
	else
		return fp .. '/'
	end
end

local LineColumnProvider = function()
	local line_column = fileinfo.line_column()
	line_column = line_column:gsub("%s+", "")
	return ' ' .. line_column
end

local PercentProvider = function()
	local line_column = fileinfo.current_line_percent()
	line_column = line_column:gsub("%s+", "")
	return  line_column .. ' ☰'
end

local BracketProvider = function(icon, cond)
	return function()
		local result

		if (cond == true or cond == false) then
			result = cond
		else
			result = cond()
		end

		if (result ~= nil and result ~= '') then
			return icon
		end
	end
end

galaxy.short_line_list = {
	'packer',
	'NvimTree',
	'floaterm',
	'fugitive',
	'fugitiveblame',
}

gls.left = {
	{
		GhostLeftBracket = {
			provider = BracketProvider(icons.rounded_left_filled, true),
			highlight = 'GalaxyViModeNestedInv'
		}
	},
	{
		Ghost = {
			provider = BracketProvider(icons.ghost, true),
			highlight = 'GalaxyViModeInv',
		},
	},
	{
		ViModeLeftBracket = {
			provider = BracketProvider(icons.rounded_right_filled, true),
			highlight = 'GalaxyViMode',
		},
	},
	{
		ViMode = {
			provider = function()
				local label, mode_color, mode_nested = unpack(get_mode())
				highlight('GalaxyViMode', mode_color, mode_nested)
				highlight('GalaxyViModeInv', mode_nested, mode_color)
				highlight('GalaxyViModeNested', mode_nested, colors.bg)
				highlight('GalaxyViModeNestedInv', colors.bg, mode_nested)
				highlight('GalaxyPercentBracket', colors.bg, mode_color)

				highlight('GalaxyGitLCBracket', mode_nested, mode_color)

				if condition.buffer_not_empty() then
					highlight('GalaxyViModeBracket', mode_nested, mode_color)
				else
					if condition.check_git_workspace() then
						highlight('GalaxyGitLCBracket', colors.bg, mode_color)
					end
					highlight('GalaxyViModeBracket', colors.bg, mode_color)
				end
				return '  ' .. label .. ' '
			end,
		},
	},
	{
		ViModeBracket = {
			provider = BracketProvider(icons.arrow_right_filled, true),
			highlight = 'GalaxyViModeBracket',
		},
	},
	{
		GitIcon = {
			provider = BracketProvider('  ' .. icons.branch .. ' ', true),
			condition = check_width_and_git_and_buffer,
			highlight = 'GalaxyViModeInv',
		}
	},
	{
		GitBranch = {
			provider = function()
				local vcs = require('galaxyline.provider_vcs')
				local branch_name = vcs.get_git_branch()
				if (not branch_name) then
					return ' no git '
				end
				if (string.len(branch_name) > 28) then
					return string.sub(branch_name, 1, 25)..'...'
				end
				return branch_name .. ' '
			end,
			condition = check_width_and_git_and_buffer,
			highlight = 'GalaxyViModeInv',
			separator = icons.arrow_right,
			separator_highlight = 'GalaxyViModeInv',
		}
	},
	{
		FileIcon = {
			provider = function()
				local icon = fileinfo.get_file_icon()
				if condition.check_git_workspace() then
					return ' ' .. icon
				end

				return '  ' .. icon
			end,
			condition = check_buffer_and_width,
			highlight = 'GalaxyViModeInv',
		},
	},
	{
		FilePath = {
			provider = FilePathShortProvider,
			condition = check_buffer_and_width,
			highlight = 'GalaxyViModeInv',
		},
	},
	{
		FileName = {
			provider = 'FileName',
			condition = check_buffer_and_width,
			highlight = 'GalaxyViModeInv',
			separator = icons.arrow_right_filled,
			separator_highlight = 'GalaxyViModeNestedInv',
		},
	},
	{
		DiffAdd = {
			provider = 'DiffAdd',
			icon = '  ',
			condition = check_width_and_git_and_buffer,
			highlight = { colors.diffAdd, colors.bg },
		},
	},
	{
		DiffModified = {
			provider = 'DiffModified',
			condition = check_width_and_git_and_buffer,
			icon = '  ',
			highlight = { colors.diffModified, colors.bg },
		},
	},
	{
		DiffRemove = {
			provider = 'DiffRemove',
			condition = check_width_and_git_and_buffer,
			icon = '  ',
			highlight = { colors.diffDeleted, colors.bg },
		},
	},
	{
		WSpace = {
			provider = 'WhiteSpace',
			highlight = { colors.bg, colors.bg },
		}
	}
}

gls.right = {
	{
		DiagnosticErrorLeftBracket = {
			provider = BracketProvider(icons.rounded_left_filled, diag.get_diagnostic_error),
			highlight = 'DiagnosticErrorInv',
		}
	},
	{
		DiagnosticError = {
			provider = function()
				local error_result = diag.get_diagnostic_error()
				highlight('DiagnosticError', colors.error, colors.bg)
				highlight('DiagnosticErrorInv', colors.bg, colors.error)

				if (error_result ~= '' and error_result ~= nil) then
					return error_result
				end
			end,
			icon = icons.error .. ' ',
			highlight = 'DiagnosticError',
			condition = check_width_and_git_and_buffer,
		}
	},
	{
		DiagnosticErrorRightBracket = {
			provider = {
				BracketProvider(icons.rounded_right_filled, diag.get_diagnostic_error),
				BracketProvider(' ', diag.get_diagnostic_error),
			},
			highlight = 'DiagnosticErrorInv',
		}
	},
	{
		DiagnosticWarnLeftBracket = {
			provider = BracketProvider(icons.rounded_left_filled, diag.get_diagnostic_warn),
			highlight = 'DiagnosticWarnInv',
		}
	},
	{
		DiagnosticWarn = {
			provider = function()
				local warn_result = diag.get_diagnostic_warn()
				highlight('DiagnosticWarn', colors.warn, colors.bg)
				highlight('DiagnosticWarnInv', colors.bg, colors.warn)

				if (warn_result ~= '' and warn_result ~= nil) then
					return warn_result
				end
			end,
			highlight = 'DiagnosticWarn',
			icon = icons.warn .. ' ',
			condition = check_width_and_git_and_buffer,
		}
	},
	{
		DiagnosticWarnRightBracket = {
			provider = {
				BracketProvider(icons.rounded_right_filled, diag.get_diagnostic_warn),
				BracketProvider(' ', diag.get_diagnostic_warn),
			},
			highlight = 'DiagnosticWarnInv',
		}
	},
	{
		DiagnosticInfoLeftBracket = {
			provider = BracketProvider(icons.rounded_left_filled, diag.get_diagnostic_info),
			highlight = 'DiagnosticInfoInv',
		}
	},
	{
		DiagnosticInfo = {
			provider = function()
				local info_result = diag.get_diagnostic_info()
				highlight('DiagnosticInfo', colors.info, colors.bg)
				highlight('DiagnosticInfoInv', colors.bg, colors.info)

				if (info_result ~= '' and info_result ~= nil) then
					return info_result
				end
			end,
			icon = icons.info .. ' ',
			highlight = 'DiagnosticInfo',
			condition = check_width_and_git_and_buffer,
		}
	},
	{
		DiagnosticInfoRightBracket = {
			provider = {
				BracketProvider(icons.rounded_right_filled, diag.get_diagnostic_info),
				BracketProvider(' ', diag.get_diagnostic_info),
			},
			highlight = 'DiagnosticInfoInv',
		}
	},
	{
		GitBranchRightBracket = {
			provider = BracketProvider(icons.arrow_left_filled, true),
			condition = check_git_and_width,
			highlight = 'GalaxyViModeNestedInv',
		}
	},
	{
		GitRoot = {
			provider = utils.get_git_root,
			condition = check_git_and_width,
			icon = '  '.. icons.file .. ' ',
			highlight = 'GalaxyViModeInv',
		},
	},
	{
		LineColumn = {
			provider = {
				LineColumnProvider,
				function() return ' ' end,
			},
			highlight = 'GalaxyViMode',
			separator = icons.arrow_left_filled,
			separator_highlight = 'GalaxyGitLCBracket',
		}
	},
	{
		PerCent = {
			provider = {
				PercentProvider,
			},
			highlight = 'GalaxyViMode',
			separator = icons.arrow_left .. ' ',
			separator_highlight = 'GalaxyViModeLeftBracket',
		},
	},
	{
		PercentRightBracket = {
			provider = BracketProvider(icons.rounded_right_filled, true),
			highlight = 'GalaxyPercentBracket',
		},
	},
}

gls.short_line_left = {
	{
		GhostLeftBracketShort = {
			provider = BracketProvider(icons.rounded_left_filled, true),
			highlight = { colors.white, colors.bg }
		}
	},
	{
		GhostShort = {
			provider = BracketProvider(icons.ghost, true),
			highlight = { colors.bg, colors.white },
		},
	},
	{
		GhostRightBracketShort = {
			provider = BracketProvider(icons.rounded_right_filled, true),
			highlight = { colors.white, colors.bg }
		}
	},
	{
		FileIconShort = {
			provider = {
				function() return '  ' end,
				'FileIcon',
			},
			condition = condition.buffer_not_empty,
			highlight = {
				require('galaxyline.provider_fileinfo').get_file_icon,
				colors.bg,
			},
		},
	},
	{
		FilePathShort = {
			provider = FilePathShortProvider,
			condition = condition.buffer_not_empty,
			highlight = { colors.white, colors.bg },
		},
	},
	{
		FileNameShort = {
			provider = 'FileName',
			condition = condition.buffer_not_empty,
			highlight = { colors.white, colors.bg },
		},
	},
}

gls.short_line_right = {
	{
		GitRootShortLeftBracket = {
			provider = BracketProvider(icons.arrow_left_filled, true),
			condition = condition.buffer_not_empty,
			highlight = { colors.white, colors.bg },
		}
	},
	{
		GitRootShort = {
			provider = utils.get_git_root,
			condition = condition.buffer_not_empty,
			icon = '  '.. icons.file .. ' ',
			highlight = { colors.bg, colors.white },
		},
	},
	{
		GitRootShortRightBracket = {
			provider = BracketProvider(icons.rounded_right_filled, true),
			condition = condition.buffer_not_empty,
			highlight = { colors.white, colors.bg },
		}
	},
}
