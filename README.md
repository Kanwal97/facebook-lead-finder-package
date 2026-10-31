# Facebook group lead finder

Finds freelance leads in Facebook groups. It searches for groups where business
owners hang out, reads their newest posts through a Chromium window logged into
your own account, and uses a local open-source model (Ollama by default) to flag
real buying intent and draft a reply. Read only: it never posts, comments,
reacts, joins or sends messages. You do all of that yourself.

## Setup (once)

```powershell
cd D:\project\python-projects\facebook-lead-finder-package
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

Then the model:

1. Install Ollama from https://ollama.com and start it.
2. `ollama pull qwen3:4b` (about 2.6 GB).
3. `python -m facebook test-ai` scores one sample post and prints the result.

Edit `config.yaml`: your profile under `profile:`, search queries under
`facebook.discovery.queries`. Everything else has safe defaults.

## Run

```powershell
python -m facebook login            # a browser opens: log in by hand, set language to English (US)
python -m facebook discover         # search for groups, score them with the model
python -m facebook groups           # review candidates
python -m facebook groups approve-above 60   # approve everything the model scored 60+
python -m facebook groups add https://www.facebook.com/groups/xyz/   # or add one by URL
python -m facebook browse           # read approved groups, score posts
python -m facebook leads --new      # unreviewed leads with draft replies
python -m facebook mark 12 replied  # also: won / lost / ignored
python -m facebook status           # budgets, cooldown, database path
```

`browse` defaults to assist mode: the tool opens each group and you scroll;
press Enter to move on. `browse --mode auto` scrolls for you, slowly. Private
groups must be joined by hand first; the tool only reads groups you can see.

## Safety

Automating a personal Facebook account is against Facebook's terms. The
tool keeps to a few page loads a day, pauses between groups, halves every
limit during the first two weeks, stops at the first block signal and then
refuses to run for 72 hours. That lowers the chance of a checkpoint but does
not remove it. Use an account you can afford to have checked.

## Tests

```powershell
python -m pytest tests -q
```
