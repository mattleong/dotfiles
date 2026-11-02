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
| `ui.py` | Drawing: colors, columns, help, and confirmation screens |
| `keyboard.py` | Key input, focus reports, line editing, and Vim motions |
| `matching.py` | Filtering and sorting |
| `worktrees.py` | Git worktree state, status, and history |
| `branching.py` | Branch lists, names, and creating worktrees |
| `tabs.py` | Rex sessions and tabs |
| `actions.py` | Operations that combine Git and Rex |
| `github.py` | Pull request status from `gh`, with a cache |
| `util.py` | Running commands, Git calls, paths, and shared state |

Run the tests from this directory:

```sh
python3 -m unittest test_worktrees
```

Rex runs the picker with the system `python3`, which can be as old as 3.9, so keep the code
compatible with it.
