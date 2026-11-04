# skyydb

A small local web app for personal trackers, each a SQLite database, built on [Datasette](https://datasette.io) in [Catppuccin Mocha](https://catppuccin.com/palette) colours. It runs on your own computer at http://127.0.0.1:8001.

- **Chinese vocabulary**: three lists (to learn, to revise, learnt) with simple spaced repetition.
- **Workouts**: sets, plans, streaks, charts and personal records.
- **Finance**: expenses against daily and monthly budgets, savings and shares.
- **Uni**: courses and deadlines, ranked by priority.
- **Quotes** and **Knowledge**: notes worth keeping, with tags.

![Home](docs/home.png)

| Chinese | Workouts |
|---|---|
| ![Chinese lists](docs/chinese.png) | ![Workout plans](docs/workouts.png) |

| Finance | Uni |
|---|---|
| ![Finance investments](docs/finance.png) | ![Uni tasks](docs/uni.png) |

| Quotes |
|---|
| ![Quotes](docs/quotes.png) |

## Install

You need Linux, Python 3.11+, SQLite, `xdg-utils` and a way to install Datasette ([uv](https://docs.astral.sh/uv/), pipx or plain pip); a CJK font (`noto-fonts-cjk`) is needed for Chinese characters. On Arch:

```bash
sudo pacman -S --needed python sqlite xdg-utils noto-fonts-cjk uv

# Datasette 1.0a41: any one of these (the `datasette` command must end up on your PATH)
uv tool install --prerelease=allow 'datasette==1.0a41'
pipx install --pip-args=--pre 'datasette==1.0a41'
python -m venv ~/.venvs/datasette && ~/.venvs/datasette/bin/pip install --pre 'datasette==1.0a41' \
  && ln -s ~/.venvs/datasette/bin/datasette ~/.local/bin/datasette

git clone https://github.com/l4n1skyy/skyydb.git
cd skyydb
ln -s "$PWD/bin/skyydb" ~/.local/bin/skyydb   # any folder on your PATH works
skyydb install finance workouts chinese       # pick what you want (leave it out for everything)
skyydb                                        # starts the app and opens the browser
```

**Pick your features.** The features are `chinese`, `workouts`, `finance`, `uni`, `quotes` and `knowledge`. `skyydb install finance workouts chinese` creates only those databases, and only those get a page and a spot on the dashboard. Run it again any time to change the choice; the data of a feature you drop stays in its file, just hidden. `skyydb features` shows what's installed.

The databases and `backups/` live inside this folder and stay out of git. Run `skyydb autostart` once to keep the app running whenever you're logged in.

## The `skyydb` command

| Command | What it does |
|---|---|
| `skyydb install [features]` | Chooses which features are installed (no names = all) |
| `skyydb features` | Lists which features are installed |
| `skyydb` | Starts the app if needed, backs up the databases and opens the browser |
| `skyydb status` | Shows whether it's running and a summary of your data |
| `skyydb import FILE` | Imports Chinese words from a CSV, or finance data from Google Sheets exports |
| `skyydb backup` | Copies the databases into `backups/` (also done once a day, keeping 14) |
| `skyydb restart` / `skyydb stop` | Restarts or stops the app (restart after editing `plugins/app.py`) |
| `skyydb autostart` | Starts the app at login (`skyydb autostart off` undoes it) |

## Notes

On the home page, today's Chinese words start face down: click one to flip it over. The l4n1skyy logo at the top left is the menu's home link. **Tab** and **Shift+Tab** cycle through the pages in the top menu, even from a form field.

Datasette only listens on 127.0.0.1, so nothing outside your computer can reach the app. The **.db** links in the top bar open Datasette's own pages, where you can browse, query and edit every table.
