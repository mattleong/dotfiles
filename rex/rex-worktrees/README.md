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
| `ui.py` | Drawing: colors, columns, fitting screens to the pane, help, error, and confirmation screens |
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

The list's letters are commands, so its filter starts only with `/`. Screens built around a text
input (the list's filter while typing, New worktree, and New branch) take every printable key as
text, and move their list with keys that can't be text: the arrows and ctrl+n/p, plus page up/down
and home/end where the list scrolls. esc clears the text, then goes back. Keep new keys within
that split. In New worktree and New branch, tab, which can't be text either, shows what enter
would do in full; confirmations use it the same way.

While you type in New worktree, its first row, which creates a branch from the text, stays
selected unless the text is an existing branch's name or the start of a word in one (see
`matching.strong`), so enter never checks out a branch you didn't mean.

Every screen keeps its key hints pinned to the bottom of the pane, and lists shorten or scroll to
fit (see `compose` and `fit_parts` in `ui.py`), so check changes in a short pane, such as 50×16, as
well as a large one. What's cut stays inspectable: each list keeps at least a line saying how many
it holds, and tab shows everything.

Rex runs the picker with the system `python3`, which can be as old as 3.9, so keep the code
compatible with it.
