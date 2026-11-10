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
| `screen_remove.py` | Removing worktrees, pruning missing ones, repairing broken ones, and cleaning up |
| `ui.py` | Drawing: color roles, labels, key hints, columns, fitting screens to the pane, help, details, and error screens |
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

Screens with a text input, the list's filter and New worktree, have two modes, as Vim does, and
start in normal mode, where letters are keys. The list's filter starts with `/`, its prompt, or `i`;
the footer names `/`. New worktree opens in normal mode too, even with text already in its input,
as when the list's filter found nothing, and `i`, `a`, or `/` starts typing at the end of the text
(the footer says `i type`); its prompt is a word, `Branch`, since no one key starts it. There, a
letter that isn't a key is never taken as text: it says `Press i to type a name.` While typing,
every printable key is text, and the list moves with keys that can't be: the arrows and ctrl+n/p,
plus page up/down and home/end. An input shows its mode: typing, its prompt is in the select role,
with the cursor after the text; not typing, the prompt is dim, with no cursor, and an empty input
says which key starts, as `/ to filter` and New worktree's `i to type a name or filter` do. esc is
the same ladder in both: typing, it stops typing and keeps the text, so the keys act on it (the
footer says `esc done`); then it clears the text; then, with none, it quits, or New worktree goes
back to the list, or quits when the picker opened straight into it. q does that last step at once.
Keep new keys within that split. In New worktree, tab, which can't be text either, shows what enter
would do in full, in either mode; confirmations use it the same way.

Every list, and help and details, moves with Vim's motions (see `keyboard.motion`): `j`/`k`,
`gg`/`G`, ctrl+d/u, ctrl+f/b, and the arrows. A count before `j` or `k` (or an arrow) moves that many rows, and
before `G` or `gg` goes to that row, as `5j` and `12G` do; `0` doesn't start one, and no screen uses
digits otherwise. Any other key drops the count and does its one thing, so `2dd` removes just the
selected worktree, and esc with a count, a `g`, or a `d` typed cancels just that. `H`, `M`, and `L`
go to the top, middle, and bottom row in view where there's a row cursor (the list, New worktree,
cleanup, and the confirmations' options); help and details scroll instead, so they have none. In
New worktree, the rows above the branches are always in view, so `H` is their first; and a branch
selected shows its plan, which shortens the list, so `L` or `M` pressed on the first row chooses
among the rows on screen as it's pressed, then the list scrolls to keep that row in view. The list
and New worktree say on the status line what a half-typed motion needs, such as `5… then j or k to
move 5 rows, or G for row 5`. There's no `n` for the next match: in the list, `n` is New worktree.

The list puts the main checkout first, then the rest by their latest commit, newest first. `S`
cycles that (`recent`) with `state` (changes, open pull requests, the rest, finished ones, then
missing ones) and `name`, keeping the main checkout first in each; the header notes any order but
the first. The choice is remembered in `$XDG_STATE_HOME/rex/worktrees.json` (by default under
`~/.local/state`); a missing or broken file just means the default order, and one that can't be
written is forgotten quietly (see `util.load_settings`). The list's ages load with the list itself,
in one Git call, so in the default order it never reorders as it appears; sorted by state, a
worktree moves once its changes or pull request are known, as they arrive after the list shows.

The BRANCH column is at most `ui.MAX_BRANCH` (40) wide, so one long name can't push the other
columns away; a longer name loses its middle (see `ui.middle`), keeping its start and its end,
where similar names differ, and the preview below the list shows it in full. FOLDER sits over the
folders' names; the tool that made a worktree, such as `claude`, is in an untitled column before it,
so the names line up even when only some rows have a tool. STATE is as wide as its widest cell, and
at least as wide as `changes`, which often arrives after the list shows, so the columns after it
don't shift when it does; while a row's status is loading, `…` shows only where the row has no label
yet, for the same reason. While a filter has text, how many worktrees it matches, such as `3 of 11`,
shows right after the text, where the whole text fits beside it; the selected row's place, such as
`8/17`, shows at the right of the row while the list scrolls. When an action, such as removing one,
leaves a filter matching nothing it matched before, the filter clears, rather than leaving an empty
list. When the main checkout is the only worktree, a line under it says `n` creates one.

The list's filter and New worktree's branch filter match each word typed against a name the same
way (see `matching.fuzzy`): the word appears in the name, or its letters appear in order, each right
after the one before or at the start of a word (after `/`, `-`, `_`, `.`, or a space, or a capital
after a lowercase letter). So `flf` finds `feat/login-flow`, and `log` finds `fix/login`, but `dark`
doesn't find `dependabot/npm_and_yarn/webpack-5.94.0`. Matches score higher for letters that follow
each other and ones that start words, so whole words rank first. The list also matches a word no
branch matches against the worktree's path, but only as a plain substring, since paths are long.

New worktree is one screen for an existing branch and a new one: the text filters the branches
that have no worktree, and names a new branch. With no text, the list shows local branches
committed to in the last 30 days, then remote-only ones from then, then everything older, each
newest first (see `branching.load_branches`), under a dim row asking for a name: `+ Press i and
type a name to create a branch`, where enter starts typing too, or while typing, `+ Type a name to
create a branch`, where enter can't act. The footer offers enter once a row can act. Text that can be a new branch gets a create row for each branch it can
start from, the default branch first, as `+ Create “fix-login” from origin/main   default branch`,
then the current branch (one row when they're the same). The current branch is read as the screen
opens, and a new branch starts from the one its row names, by its full name (`refs/heads/main`), or
from the commit when it's detached, not from wherever `HEAD` is by the time enter is pressed. The
row shows the name as it will be, so `Fix the login` reads `Fix-the-login`, and a long one loses its
middle alike in every row. Enter acts on the selected row at once, and the plan says exactly what
it does, a sentence to a row after a dim label: `Creates` the branch from its base, or tracking a
remote branch, `Checks out` a local one, or `Opens` a worktree; `In` its folder; `Then runs` the
setup command (`git config rex.worktreeSetup`). The plan is pinned to the bottom of the pane, right
above the footer and its `enter` (a blank row between them where there's room), so it never moves
as the list shortens; the list has the rows above it. How many branches the text matches, such as
`2 of 40`, shows right after the text, as in the list's filter. When what was typed last is
something the name leaves out, such as `/` in `fix/` on the way to `fix/login`, or `-`, `~`, `:`,
`?`, or `@{` (see `branching.left_out`), the text gets a dim hint to type the rest rather than a
row that would create `fix`; spaces at the end don't count.

The screen isn't drawn while keys are waiting, as when text is pasted, so it keeps up; but enter
acts only on a frame that was drawn with nothing waiting. One that was waiting, such as the newline
at the end of pasted text, draws the frame instead and is dropped, so you see the row and the plan
and press enter again. That holds in both modes, so a paste that starts typing, such as `i` or `/`,
then a name and a newline, types the name and creates nothing (held by
`test_enter_acts_only_on_a_frame_that_was_drawn`). A name pasted in normal mode isn't text, though
an `i` in it starts typing, as in Vim, and the rest goes in. The confirmations that remove, prune, or repair drop an enter that was
waiting before they were drawn the same way, so `dd` or `c` followed at once by enter, as in pasted
text, never confirms something nobody saw.

While you type, the first create row stays selected, or the create row you typed on from, unless
the text is an existing branch's name or the start of a word in one (see `matching.strong`), so
enter never checks out a branch you didn't mean. A name typed in full beats longer names that start
with it: a listed branch goes first and is selected (see `matching.exact`), and a checked-out one,
which isn't listed, gets a row of its own, `Open the worktree for “fix/a”`, which stays selected,
even typed with spaces or other capitals, such as `fix a` for `fix/a`. Of branches with the same
name on several remotes, `checkout.defaultRemote`'s, else origin's, goes first. Text such as
`origin/fix`, as `git branch -r` lists branches, matches the branches on that remote by the rest
(see `branching.query_for`), never another remote's, even one with the same name, and never
creates a local `origin/…` branch; `origin/` alone lists all of origin's branches. When `fork/x`
means a local `x` that tracks another remote and is at another commit, a dim note says so, leading
with the difference, and tab shows it in full.

Text that can't be a new branch gets one dim row in place of the create rows, with a note under it
saying why: it names a branch that exists, or one checked out in a worktree whose folder is
missing; it's a remote's name, or starts with one; it would clash with a branch, as `feat` does
with `feat/x`; or nothing in it can be a name. While an existing branch is selected, the note is
dim, since enter goes there instead. Only when that row is selected, or enter is pressed on it,
does the note take its warning's role: red, the danger role, for text that can't go anywhere, and
for a remote's name alone, which is most likely on the way to `origin/…`, a hint that stays dim
until enter makes it stand out. Where enter can't act, the footer leaves it out, and pressing it
makes the reason stand out.

When creating fails, the screen stays, with Git's message, and lists the branches again, so the
rows and the plan are made afresh: a folder that appeared since the plan named it means another
folder, and a branch that appeared means selecting it. Before Git runs, `branching.create_worktree`
checks the folder is still free, since Git would create the new branch before finding it taken.

Branches only on a remote say so in the FROM column: `remote` for origin, or the remote's name,
for every remote when more than one has branches. The columns are sized to every branch that
matches, not just the rows in view, so they stay put while the list scrolls.

Two keys remove worktrees. `dd` removes the selected one once you confirm, closing the panes
inside it. When it offers to delete the branch too, because it's merged into the default branch,
that's checked again right before the branch is deleted (see `actions.remove_worktree`): a commit
that landed on it while the screen was open keeps the branch, and the message says so. `c` cleans
up: it offers every worktree whose pull request merged or whose remote branch is gone, and every
one whose folder is missing, each with its reason, in the list's order, with the cursor on the
first row that can be checked. A finished one starts checked unless it has uncommitted changes, and
cleanup never discards changes, so those can't be checked: `dd` removes one, once “Discard
uncommitted changes” is checked. A missing one starts unchecked, since its folder may be away only
for now, as on a drive that isn't mounted; check it to prune it, or prune it on its own with `dd`.
The list's `c clean up 3` counts what enter would remove as cleanup opens; when cleanup has only
rows that start unchecked, such as missing ones, it reads `c clean up`, and cleanup's title says
`nothing checked yet`. Its option to delete branches counts only the rows it can apply to (not
missing ones, nor ones with changes), so it reads “Also delete its branch” when that's one. Neither
key removes the main checkout or a locked worktree; `dd` isn't offered there, and the first `d`
says why.

A worktree whose folder is missing is pruned rather than removed, by `dd` (the footer reads
`dd prune` on it) or by cleanup. Pruning drops only Git's record of the worktree: its branch is
kept, and nothing closes. It runs `git worktree remove` on that one worktree, which, when its folder
is gone, deletes just its record, without `--force` (checked with Git 2.50, and held by
`test_prune_drops_only_the_one_missing_worktree`), where `git worktree prune` would drop every
missing worktree at once. A folder that's back at the path isn't pruned, since removing the
worktree would delete it (see `actions.prune_worktree`); the message says so, and the list, listed
again, shows the worktree as it is. Git keeps a locked worktree whose folder is missing, as for one
on a drive that isn't mounted, and so does the picker.

A worktree whose folder is there but whose `.git` file is gone is `broken` (see
`worktrees.broken`): Git calls it prunable, can't use it, and refuses to remove it, and pruning it
would drop Git's record and leave the folder behind. So it isn't missing, and cleanup doesn't offer
it. The list labels it, its preview says what's wrong instead of listing commits (Git would read
another checkout's there, for a folder inside one), and enter, `s`, `o`, and `e` say why they can't
open it. `dd` repairs it (the footer reads `dd repair`): `git worktree repair` writes the `.git`
file again, and the folder's files stay as they are. Git repairs every worktree without its `.git`
file at once, and can't be asked for just one (given a path, it repairs them all, then fails for
that path; checked with Git 2.50), so the confirmation lists the others it repairs too (see
`actions.repair_worktree`).

Removing, pruning, and repairing confirm in the same layout: what will go or change, then what
that costs (uncommitted changes and the panes that will close), then the options. A note under the
options, such as “The branch will be kept: …”, is dim and starts where the options' labels start,
after a blank row, so it can't be read as an option, or as being about the option above it. The
hint for “Discard uncommitted changes” sits right under that checkbox, and enter pressed without it
checked makes the hint stand out and moves to it. Cleanup lists what will go, each row with what
removing it closes, then its option to delete branches, which never applies to the pruned ones,
then the selected row explained in full, starting where the options' labels do, as the option's own
note does. When only part of a removal works, the message says what was done, then what wasn't: Git
can drop a worktree from its list and still fail to delete some of its folder, and a branch can be
kept or fail to delete, or a pane fail to close (see `actions.remove_worktree` and
`actions.remove_many`).

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
| danger | red | a missing folder, a broken worktree, errors, a closed PR, “Discard uncommitted changes” |
| finished | magenta | a merged PR, a remote branch that's gone |
| source | blue | the main checkout, remotes, the tools that made worktrees, such as `rex`, a commit's author |
| dim | dim | metadata: ages, paths, hints, a draft PR, `locked` |
| hash | yellow | a commit's hash, the color `git log` gives it |

Yellow has that second meaning only in commit lists, where nothing else is yellow, so a hash can't
be taken for a warning.

Ahead and behind counts (`↑2 ↓3`) are plain text, since they're facts rather than states, and
text that matches a filter is bold and underlined, with no color of its own.

Each state has one label, the same at every width (see `ui.LABELS`); only the narrowest panes
shorten them, to their icons. Help explains each one, and sentences such as the list's summary and
messages use the same words: `2 with changes · 1 gone · 1 missing · 1 broken`.

| State | Label |
| --- | --- |
| the main checkout | `main` |
| a Rex pane is open in it | `tab` |
| uncommitted changes | `changes` |
| locked | `locked` |
| its folder is missing | `missing` |
| its folder is there, but its `.git` file is gone | `broken` |
| its remote branch was deleted | `gone` |
| ahead / behind | `↑2` / `↓3` |

Titles name the action, without a question mark, such as `Remove worktree` and `Clean up
worktrees`, and a count of the same thing reads the same wherever it shows.

Keys mean the same on every screen. enter is the primary action, and the footer names what it
does right now, such as `open`, `create`, or `remove 2`. tab shows details in full. esc steps back
a level, one level a press, and q quits or cancels where letters are commands. The footer lists
only the keys that would act right now, and one it leaves out still says why when it's pressed, or,
for enter on a confirmation or in New worktree, makes the reason on screen stand out. A move with
nowhere to go, such as `j` in a one-row list, isn't listed and just doesn't move. A screen decides
this once for each frame, in a `ui.Keys`: each key it handles, with its footer label and whether
it can act, and if not, why. The footer lists the ones that can, and the key handler asks
`Keys.refused` before acting, so the two can't disagree. `test_footers_list_only_keys_that_can_act`
holds the rule, pressing every screen's moves and tab as well as its actions; give it a scenario
when a screen's keys change.

A message sits next to what it's about, such as a count beside its input or a hint under its
checkbox. The status line above the footer is for the result of an action, such as `Removed x.`,
and for errors.
