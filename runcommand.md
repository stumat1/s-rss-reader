# Running locally (Windows / PowerShell)

Run these commands once from the project root to create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If Activate.ps1 is blocked by execution policy, run this first (once, as your user):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then start the app:

```powershell
$env:DB_PATH = ".\data\rss.db"
uvicorn app.main:app --reload
```

Open http://localhost:8000 in your browser.

---

On subsequent runs, just activate the venv and start the server:

```powershell
.\.venv\Scripts\Activate.ps1
$env:DB_PATH = ".\data\rss.db"
uvicorn app.main:app --reload
```
