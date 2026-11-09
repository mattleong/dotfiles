# rex-worktrees

The Git worktree picker for Rex. `Ctrl+B, w` (see `../init.lua`) opens it in a floating pane by
running this directory with `python3`.

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
| `util.py` | Running commands, Git calls, paths, and shared state |

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
and home/end where the list scrolls. esc clears the text, then goes back. Keep new keys within
that split. In New worktree and New branch, tab, which can't be text either, shows what enter
would do in full; confirmations use it the same way.

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
a level, and q quits or cancels where letters are commands. (The list's filter still differs for
now: tab stops typing there, and esc clears the filter as it stops.) The footer lists only the keys
that would act right now; one it leaves out still says why when it's pressed. A screen decides
this once for each frame, in a `ui.Keys`: each key it handles, with its footer label and whether
it can act, and if not, why. The footer lists the ones that can, and the key handler asks
`Keys.refused` before acting, so the two can't disagree. `test_footers_list_only_keys_that_can_act`
holds the rule; give it a scenario when a screen's keys change.

A message sits next to what it's about, such as a count beside its input or a hint under its
checkbox. The status line above the footer is for the result of an action, such as `Removed x.`,
and for errors.
