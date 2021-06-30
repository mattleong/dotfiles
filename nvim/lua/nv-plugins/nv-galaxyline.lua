local galaxy = require('galaxyline');
local gls = galaxy.section
local diag = require('galaxyline.provider_diagnostic')
local condition = require 'galaxyline.condition'
local fileinfo = require('galaxyline.provider_fileinfo')
local utils = require('nv-utils');
local highlight = utils.highlight;

local colors = {
	brown = '#a9323d',
	aqua = '#5b9c9c',
	beige = '#686765',
	yellow = '#a8a384',
	pink = '#9e619e',
	green = '#63976f',
	white = '#9ea3c0',
	bg = '#111219',
	blue = '#494f8b',
	lightBlue = '#959acb',
	purple = '#5e3e5e',
	info = '#5d8fac',
	error = '#c2616b',
	warn = '#c59f96',
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
}

local get_mode = function()
	local mode_colors = {
		[110] = { 'NORMAL', colors.blue, colors.lightBlue },
		[105] = { 'INSERT', colors.purple, colors.pink },
		[99] = { 'COMMAND', colors.beige, colors.yellow },
		[116] = { 'TERMINAL', colors.aqua, colors.info },
		[118] = { 'VISUAL', colors.purple, colors.info, },
		[22] = { 'V-BLOCK', colors.purple, colors.info, },
		[86] = { 'V-LINE', colors.purple, colors.info, },
		[82] = { 'REPLACE', colors.brown, colors.error, },
		[115] = { 'SELECT', colors.brown, colors.error, },
		[83] = { 'S-LINE', colors.brown, colors.error, },
	}

	local mode_data = mode_colors[vim.fn.mode():byte()]
	if mode_data ~= nil then
		return mode_data
	end
end

local check_width_and_git_and_buffer = function()
	return condition.hide_in_width() and condition.check_git_workspace() and condition.buffer_not_empty()
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
	return '☰' .. line_column
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
				highlight('GalaxyBracket', colors.bg, mode_color)

				return '  ' .. label .. ' '
			end,
		},
	},
	{
		ViModeRightBracket = {
			provider = BracketProvider(icons.arrow_right_filled, true),
			highlight = 'GalaxyViModeInv',
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
			separator = icons.arrow_right .. ' ',
			separator_highlight = 'GalaxyViModeInv',
		}
	},
	{
		FileIcon = {
			provider = 'FileIcon',
			condition = check_width_and_git_and_buffer,
			highlight = 'GalaxyViModeInv',
		},
	},
	{
		FilePath = {
			provider = FilePathShortProvider,
			condition = check_width_and_git_and_buffer,
			highlight = 'GalaxyViModeInv',
		},
	},
	{
		FileName = {
			provider = 'FileName',
			condition = check_width_and_git_and_buffer,
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
			highlight = { colors.green, colors.bg },
		},
	},
	{
		DiffModified = {
			provider = 'DiffModified',
			condition = check_width_and_git_and_buffer,
			icon = '  ',
			highlight = { colors.warn, colors.bg },
		},
	},
	{
		DiffRemove = {
			provider = 'DiffRemove',
			condition = check_width_and_git_and_buffer,
			icon = '  ',
			highlight = { colors.error, colors.bg },
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
			highlight = 'GalaxyViModeNestedInv',
		}
	},
	{
		GitRoot = {
			provider = utils.get_git_root,
			condition = condition.buffer_not_empty,
			icon = '   ',
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
			separator_highlight = 'GalaxyViModeInv',
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
			highlight = 'GalaxyBracket',
		},
	},
}

gls.short_line_left = {
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
			highlight = 'GalaxyGitRootShortInv'
		}
	},
	{
		GitRootShort = {
			provider = utils.get_git_root,
			condition = condition.buffer_not_empty,
			icon = '   ',
			highlight = { colors.bg, colors.white },
		},
	},
	{
		GitRootShortRightBracket = {
			provider = BracketProvider(icons.rounded_right_filled, true),
			highlight = 'GalaxyGitRootShortInv'
		}
	},
}
