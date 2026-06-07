# GastroScout AI

Restaurant menu scout for Zagreb lunch decisions. Scrapes daily menus from favorite spots or discovers nearby restaurants via Google Maps, extracts **full menus**, and displays them in a Streamlit UI.

---

## What it does

Two pipelines (switch in the sidebar or via CLI):

| Mode | Description |
|------|-------------|
| **Favorites (default)** | Scrapes daily gableci from **Fakin**, **Lobby**, and **Cassandra** |
| **Nearby (Google Maps)** | Finds restaurants within a radius, sorts by **Google rating**, scrapes **full a la carte menus** from websites |

### Nearby pipeline flow

```
Your location (browser GPS in app / IP in CLI)
        ↓
Google Places: all restaurants within radius (e.g. 3 km)
        ↓
Sort by Google rating (highest first)
        ↓
For each restaurant (until N menus found):
  → Find menu page (/menu, /jelovnik, /menu-a-la-carte, nav links…)
  → Scrape with Playwright (waits for JS menus)
  → Parse full menu locally (name + price per dish)
  → Skip if no scrapeable menu; try next rated restaurant
        ↓
Show top N restaurants with complete menus in UI
```

---

## Project structure

```
restaurant_agent/
├── app.py              # Streamlit UI (main entry point)
├── scout.py            # Scraping + menu extraction (Playwright, Gemini, local parser)
├── maps_scout.py       # Google Maps discovery (radius, rating sort)
├── config.py           # Pydantic schema + Gemini prompts
├── requirements.txt    # Python dependencies
├── .env.example        # API key template (copy to .env)
```

---

## Requirements

- **Python 3.11+** (3.13 tested)
- **Google Gemini API key** — menu parsing for daily gableci
- **Google Maps API key** — Places + Geocoding (enable *Places API* and *Geocoding API*)
- **Firecrawl API key** — fast scrape for simple sites (Fakin)
- **Playwright Chromium** — JS-heavy sites (Lobby, Cassandra, nearby restaurants)

---

## Setup (fresh machine)

### 1. Unzip and open the project

```powershell
cd path\to\restaurant_agent
```

### 2. Create virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```powershell
pip install -r requirements.txt
playwright install chromium
```

`playwright install chromium` is **required once** — without it, Lobby, Cassandra, and nearby scraping will fail.

### 4. Configure API keys

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```env
GOOGLE_API_KEY=...
GOOGLE_MAPS_API_KEY=...
FIRECRAWL_API_KEY=...
```

Never commit `.env` or share it in a zip — only `.env.example`.

---

## Running the app

### Streamlit UI (recommended)

**Favorites mode (default):**

```powershell
streamlit run app.py
```

**Start directly in Nearby mode** (pick one):

```powershell
# Sidebar still works; this only sets the default tab
streamlit run app.py -- --gmaps

# Or via environment variable
$env:GASTROSCOUT_MODE="gmaps"
streamlit run app.py
```

> **Note:** Streamlit eats flags like `--gmaps` unless you pass them after `--`.

**Nearby mode steps in the UI:**

1. Sidebar → **Nearby (Google Maps)**
2. Click the location button → **Allow** browser location
3. Adjust search radius slider if needed
4. Click **Scan Top Rated Nearby**
5. Wait — each restaurant takes ~30–60 s to scrape

### CLI

**Favorites:**

```powershell
python scout.py
```

**Nearby (uses laptop IP for location):**

```powershell
python scout.py --gmaps
```

---

## Tweaking behavior

### Number of restaurants (nearby mode)

Default: **3** restaurants with menus.

| Where | Variable | Default |
|-------|----------|---------|
| `app.py` | `TARGET_RESTAURANT_COUNT` | `3` |
| `maps_scout.py` | `DEFAULT_TARGET_COUNT` | `3` |
| `scout.py` (CLI) | `target=` in `discover_restaurants_with_menus(...)` | `3` |

Change all three if you want UI and CLI to match.

### Search radius

Default: **3000 m** (3 km).

| Where | Variable | Notes |
|-------|----------|-------|
| `app.py` | `DEFAULT_RADIUS_M` | Default slider value |
| `maps_scout.py` | `DEFAULT_RADIUS_M` | Google Places search |
| `scout.py` (CLI) | `radius=` argument | Hardcoded `3000` in `__main__` |

In the UI you can also change radius with the slider before scanning.

### Favorite restaurants

Edit `HARDCODED_RESTAURANTS` in `scout.py`:

```python
HARDCODED_RESTAURANTS = [
    {"name": "Fakin", "url": "https://pivovara-medvedgrad.hr/fakin/gableci"},
    {"name": "Lobby", "url": "https://lobby-eurotower.skubacz.pl/restauracja/..."},
    {"name": "Cassandra", "url": "https://www.cassandra.hr/restoran-cassandra/dnevni-jelovnici/"},
]
```

Lobby URL includes today's weekday hash — it is built automatically at import time.

### Gemini prompts

Edit `MENU_PROMPT` and `REGULAR_MENU_PROMPT` in `config.py`.

### Menu scraping depth

In `scout.py`:

| Constant | Default | Purpose |
|----------|---------|---------|
| `MAX_PAGES_TO_TRY` | `8` | Max menu URLs tried per site |
| `MAX_MENU_CHARS` | `50000` | Max text sent to Gemini fallback |
| `MENU_PATHS` | `/menu`, `/jelovnik`, … | Paths tried on each domain |

---

## Output files

| File | Written when |
|------|----------------|
| `favorites_menu.json` | After favorites scan in UI |
| `nearby_menu.json` | After nearby scan in UI |

Both are date-stamped — the UI only shows today's cache. Delete them to force a fresh scan.

---

## Known limitations

- **Lobby & Cassandra on weekends** — often closed; “no menu today” is correct, not a bug.
- **Nearby scan is slow** — Playwright opens real browser pages; 3 restaurants ≈ 2–5 minutes.
- **Not every restaurant has a website menu** — Facebook-only or booking-only sites are skipped; the pipeline tries the next rated place.
- **CLI nearby location** — uses public IP geolocation (less precise than browser GPS in the app).
- **Gemini SDK** — uses `google-genai` (`from google import genai`).

---

## Zipping / sharing the project

Include:

- All `.py` files, `config.py`, `requirements.txt`, `.env.example`, `README.md`, `.gitignore`

Exclude:

- `.venv/` (recreate on target machine)
- `.env` (secrets)
- `favorites_menu.json`, `nearby_menu.json` (runtime cache)
- `__pycache__/`

Recipient setup: follow **Setup (fresh machine)** above.

---

## Legacy version

Files `app_old.py` and `scout_old.py` are the pre–Google Maps pipeline (favorites only, simpler UI). The current pipeline uses `app.py` + `scout.py` + `maps_scout.py`.

```powershell
streamlit run app_old.py
python scout_old.py
```

---

## Quick troubleshooting

| Problem | Fix |
|---------|-----|
| `playwright` browser not found | Run `playwright install chromium` |
| Streamlit `--gmaps` not recognized | Use `streamlit run app.py -- --gmaps` |
| No nearby results | Increase radius; allow browser location; check Maps API billing |
| Empty favorites for Lobby/Cassandra | Normal on Sunday / outside kitchen hours |
| Only partial menu | Ensure menu page loads (check terminal for `Using menu page: …`) |
