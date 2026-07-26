# How to get the latest updates onto your main computer

This guide is for a non-developer using this project on two machines:

1. **AI / work machine** — where code changes are made (and pushed to GitHub when ready)
2. **Main computer** — where you actually run the rebalancer day to day

You are **not** “patching” in the technical sense. You are **updating** (syncing) a copy of the project from GitHub.

GitHub holds **safe** project files only (code, tests, docs, examples).  
It does **not** hold your real money settings, live broker exports, or private agent notes.

---

## What stays private (never from GitHub)

Keep these only on your own computers (or copy them yourself carefully, offline):

| File / folder | Why |
|---------------|-----|
| `settings.json` | Real reserves, account names, portfolio config |
| Real `Portfolio_Positions_*.csv` (broker exports) | Live holdings |
| `.scratch/` | Local agent tickets / notes (may include personal context) |

Safe to get from GitHub: Python scripts, tests, `settings.example.json`, docs (including this file), and example fixtures under `tests/`.

---

## One-time setup on your main computer

Do this once per computer. Skip steps you already finished.

### 1. Install Git (if needed)

- **Windows:** install [Git for Windows](https://git-scm.com/download/win), then open **Git Bash** or PowerShell.
- **Mac:** open Terminal and run `git --version`. If prompted to install developer tools, accept.
- **Linux:** install with your package manager (e.g. `sudo apt install git`).

### 2. Get the project folder

**If you already cloned this repo before**, open a terminal in that folder and go to [Every time you want updates](#every-time-you-want-updates).

**If this is the first time on this computer:**

```bash
cd ~/Documents
# or any folder you prefer
git clone git@github.com:SimonJester/rebal.git
cd rebal
```

If SSH keys are not set up, use HTTPS instead:

```bash
git clone https://github.com/SimonJester/rebal.git
cd rebal
```

### 3. Install Python dependencies (first time, or after major changes)

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Create your local settings (first time only)

```bash
cp settings.example.json settings.json
```

Then edit `settings.json` for **this** computer. Do not commit it. Do not upload it to GitHub.

---

## Every time you want updates

On your **main computer**, open a terminal in the project folder:

```bash
cd /path/to/rebal
```

**Option A — helper script (easiest):**

```bash
./scripts/update-from-github.sh
```

**Option B — one command:**

```bash
git pull
```

That downloads the latest **safe** files from GitHub into this folder. Your `settings.json` and real portfolio CSVs are left alone (they are not on GitHub).

After a successful update you can run the tool as usual:

```bash
source .venv/bin/activate   # if the venv is not already active
python rebal.py
```

If `requirements.txt` changed, reinstall deps once:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Which machine does what?

| Machine | Usual action |
|---------|----------------|
| AI / work machine | Make code changes → commit **safe** files only → `git push` to GitHub |
| Main computer | Only **pull** updates (`./scripts/update-from-github.sh` or `git pull`) → run `rebal.py` |

You do **not** need to push from the main computer unless you intentionally change code there.

---

## If something goes wrong

### “Please commit your changes or stash them before you merge”

You (or a tool) changed a tracked file on the main computer. Options:

1. If those changes were accidental:  
   `git status` — note the files — then ask for help before discarding anything.
2. If you want to keep local edits temporarily:  
   `git stash` then `git pull` then `git stash pop` (can be confusing; get help if unsure).

### “merge conflict” / “Automatic merge failed”

Git could not combine remote and local edits to the same lines. Stop and get help rather than guessing. Do not run force commands (`git reset --hard`, `git push --force`) unless you understand they can delete work.

### “Permission denied (publickey)” when cloning or pulling with SSH

Use the HTTPS clone/pull URL, or set up SSH keys for GitHub.

### The update script says “not a git repository”

You are not inside the `rebal` project folder. `cd` into it first.

### I need my real settings on a new computer

Copy `settings.json` yourself (USB, encrypted backup, etc.). Never put it in a public or shared git commit.

---

## Where is this file?

After you pull, open:

`docs/HOW-TO-UPDATE.md`

in the project on your main computer. That is the copy you should re-read when you forget the steps.
