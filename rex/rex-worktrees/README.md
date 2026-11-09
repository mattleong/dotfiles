# rex-worktrees

The Git worktree picker for Rex. `Ctrl+B, w` (see `../init.lua`) opens it in a floating pane by
running this directory with `python3`. The float is sized for the picker's screens, about 120
columns by 32 rows, centred, and never more than 90% of the window either way; `init.lua` works
out the window's size in cells from the pane the picker was opened from (its size over its share
of the window's layout), and falls back to a fixed share of the window when it can't.

| Module | Contents |
| --- | --- |
| `__main__.py` | Entry point: arguments, environment, and curses setup |
| `app.py` | `Manager`: the picker's state and background loading |
| `screen_list.py` | The worktree list and commit preview |
| `screen_new.py` | Creating worktrees from existing or new branches |
| `screen_remove.py` | Removing, cleaning up, and pruning worktrees |
| `ui.py` | Drawing: color roles, labels, key hints, columns, fitting screens to the pane, help, error, and confirmation screens |
| `keyboard.py` | Key input, focus reports, line editing, and Vim motions |
| `matching.py` | Filtering and sorting |
| `worktrees.py` | Git worktree state, status, and history |
| `branching.py` | Branch lists, names, and creating worktrees |
| `tabs.py` | Rex sessions, tabs, and panes: finding, opening, and closing them |
| `actions.py` | Operations that combine Git and Rex |
| `github.py` | Pull request status from `gh`, with a cache |
| `util.py` | Running commands, Git calls, paths, shared state, and choices remembered across runs |

Run the tests from this directory:

```sh
python3 -m unittest test_worktrees
```

`test_every_screen_fits_any_width_and_name` renders every screen at widths from 20 to 160 with
awkward names (empty, one character, very long, unicode, spaces); keep it passing when you add a
screen or change how one fits.

The list's letters are commands, so its filter starts only with `/`. Screens built around a text
input (the list's filter while typing, New worktree, and New branch) take every printable key as
text, and move their list with keys that can't be text: the arrows and ctrl+n/p, plus page up/down
and home/end where the list scrolls. In New worktree and New branch, esc clears the text, then goes
back. In the list's filter, esc stops typing and keeps the filter, so the list's keys act on what
it found (the footer says `esc done`); with the filter kept, esc clears it, and with no filter, esc
quits. Keep new keys within that split. In New worktree and New branch, tab, which can't be text
either, shows what enter would do in full; confirmations use it the same way.

The list puts the main checkout first, then the rest by their latest commit, newest first. `S`
cycles that (`recent`) with `state` (changes, open pull requests, the rest, finished ones, then
missing ones) and `name`, keeping the main checkout first in each; the header notes any order but
the first. The choice is remembered in `$XDG_STATE_HOME/rex/worktrees.json` (by default under
`~/.local/state`); a missing or broken file just means the default order, and one that can't be
written is forgotten quietly (see `util.load_settings`). The list's ages load with the list itself,
in one Git call, so it never reorders as it appears.

The BRANCH column is at most `ui.MAX_BRANCH` (40) wide, so one long name can't push the other
columns away; a longer name loses its middle (see `ui.middle`), keeping its start and its end,
where similar names differ, and the preview below the list shows it in full. FOLDER sits over the
folders' names; the tool that made a worktree, such as `claude`, is in an untitled column before it.
While a filter has text, how many worktrees it matches, such as `3 of 11`, shows at the right of its
row. When an action, such as removing one, leaves a filter matching nothing it matched before, the
filter clears, rather than leaving an empty list. When the main checkout is the only worktree, a
line under it says `n` creates one.

The list's filter and New worktree's branch filter match each word typed against a name the same
way (see `matching.fuzzy`): the word appears in the name, or its letters appear in order, each right
after the one before or at the start of a word (after `/`, `-`, `_`, `.`, or a space, or a capital
after a lowercase letter). So `flf` finds `feat/login-flow`, and `log` finds `fix/login`, but `dark`
doesn't find `dependabot/npm_and_yarn/webpack-5.94.0`. Matches score higher for letters that follow
each other and ones that start words, so whole words rank first. The list also matches a word no
branch matches against the worktree's path, but only as a plain substring, since paths are long.

While you type in New worktree, its first row, which creates a branch from the text, stays
selected unless the text is an existing branch's name or the start of a word in one (see
`matching.strong`), so enter never checks out a branch you didn't mean. A name typed in full beats
longer names that start with it: a listed branch goes first and is selected (see `matching.exact`),
and a checked-out one, which isn't listed, keeps the first row selected, since that row opens it,
even typed with spaces or other capitals, such as `fix a` for `fix/a`. Of branches with the same
name on several remotes, `checkout.defaultRemote`'s, else origin's, goes first. Text such as `origin/fix`,
as `git branch -r` lists branches, matches the branches on that remote by the rest (see
`branching.query_for`), never another remote's, even one with the same name, and never creates a
local `origin/…` branch; `origin/` alone lists all of origin's branches. When `fork/x` means a
local `x` that tracks another remote and is at another commit, a dim note says so, leading with
the difference, and tab shows it in full. A remote's name alone
can't be a new branch either, but it's most likely on the way to `origin/…`, so its note is a dim
hint. When the text names a branch that exists, the note under the first row is dim too, not an
error, because enter goes to that branch: it checks out the selected branch, or opens the worktree
a checked-out one is in, and the first row then says `→ Open the worktree for “…”`. Red, the danger
role, is for text that can't go anywhere, such as a branch checked out in a worktree whose folder is
missing. Where enter can't go anywhere, the footer leaves it out, and pressing it makes the note
stand out.
Branches only on a remote say so in the FROM column: `remote` for origin, or the remote's name,
for every remote when more than one has branches. The columns are sized to every branch that
matches, not just the rows in view, so they stay put while the list scrolls.

Every screen keeps its key hints pinned to the bottom of the pane, and lists shorten or scroll to
fit (see `compose` and `fit_parts` in `ui.py`), so check changes in a short pane, such as 50×16, as
well as a large one. What's cut stays inspectable: each list keeps at least a line saying how many
it holds, and tab shows everything. Where a footer has no room for `tab details`, the cut text
says so itself, as `(tab lists all)` or `(tab shows all)` (`ui.TAB_CUE`). In the details of a screen that removes
something, everything is in full: file names, tab names, and paths wrap rather than being cut, so a
screen's `parts` function takes whether to show them in full (see `show_details`).

Text wraps at spaces, so a branch such as `fix/login-flow` stays whole; only a word too long for a
line breaks, after a slash, or a dash where that's not enough (see `ui.wrap`). Spaces inside a line
are kept as they are, since they can matter in a command. A message's or note's
later lines start under its text, not its icon (see `ui.hang`).

Rex runs the picker with the system `python3`, which can be as old as 3.9, so keep the code
compatible with it.

## Design rules

Every screen follows these, so a color, a word, or a key means the same thing wherever it shows.

Colors are roles. Screens ask `ui.style` for a role, never a color: only `ui.py` maps roles to
colors (see `ui.ROLES`), and a test checks that no other module names one.

| Role | Color | Used for |
| --- | --- | --- |
| select | cyan | the `›` pointer, input prompts, checkboxes, the spinner, the sort note |
| live | green | the current worktree's `●`, `tab`, an open PR, success messages, the create row |
| attention | yellow | uncommitted changes, warning headings, a key pressed where it can't act |
| danger | red | a missing folder, errors, a closed PR, “Discard uncommitted changes” |
| finished | magenta | a merged PR, a remote branch that's gone |
| source | blue | the main checkout, remotes, and the tools that made worktrees, such as `rex` |
| dim | dim | metadata: ages, paths, hints, a draft PR, `locked`, commit hashes and authors |

Ahead and behind counts (`↑2 ↓3`) are plain text, since they're facts rather than states, and
text that matches a filter is bold and underlined, with no color of its own.

Each state has one label, the same at every width (see `ui.LABELS`); only the narrowest panes
shorten them, to their icons. Help explains each one, and sentences such as the list's summary and
messages use the same words: `2 with changes · 1 gone · 1 missing`.

| State | Label |
| --- | --- |
| the main checkout | `main` |
| a Rex pane is open in it | `tab` |
| uncommitted changes | `changes` |
| locked | `locked` |
| its folder is missing | `missing` |
| its remote branch was deleted | `gone` |
| ahead / behind | `↑2` / `↓3` |

Titles name the action, without a question mark, such as `Remove worktree` and `Clean up
worktrees`, and a count of the same thing reads the same wherever it shows.

Keys mean the same on every screen. enter is the primary action, and the footer names what it
does right now, such as `open`, `create`, or `remove 2`. tab shows details in full. esc steps back
a level, one level a press, and q quits or cancels where letters are commands. The footer lists
only the keys that would act right now; one it leaves out still says why when it's pressed. A screen decides
this once for each frame, in a `ui.Keys`: each key it handles, with its footer label and whether
it can act, and if not, why. The footer lists the ones that can, and the key handler asks
`Keys.refused` before acting, so the two can't disagree. `test_footers_list_only_keys_that_can_act`
holds the rule; give it a scenario when a screen's keys change.

A message sits next to what it's about, such as a count beside its input or a hint under its
checkbox. The status line above the footer is for the result of an action, such as `Removed x.`,
and for errors.
