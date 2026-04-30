# JobTracker

An AI-powered job application tracker that automatically extracts and organizes job-related communications from Gmail using Claude AI.

## Features

- **AI Email Scanning** — connects to Gmail via OAuth and uses Claude (tool use API) to detect job-related emails, extract company, role, status, interview dates, and skills
- **Application Dashboard** — card-based view of all tracked applications with status filtering and inline editing
- **Skills Extraction** — Claude extracts required skills/tech stack from each email; shown as badges per application card
- **Analytics** — application pipeline funnel, applications-over-time chart, and top skills frequency chart with conversion rate KPIs
- **Smart Status Management** — status only advances forward (applied → in_process → interview_scheduled → offer/rejected); terminal statuses are never overwritten
- **Demo Mode** — runs without Google credentials using sample data

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask, Flask-SQLAlchemy, Flask-Login |
| Database | SQLite |
| AI | Anthropic Claude Haiku (tool use) |
| Auth | Google OAuth 2.0 |
| Email | Gmail API (read-only) |
| Frontend | Jinja2, Bootstrap 5, Chart.js |

## Project Structure

```
job-tracker/
├── app.py          # Flask routes: auth, dashboard, agent scan, analytics, CRUD
├── agent.py        # Gmail integration + Claude tool-use pipeline
├── models.py       # SQLAlchemy models: User, JobApplication
├── requirements.txt
├── .env.example
└── templates/
    ├── base.html       # Navbar, shared styles
    ├── login.html      # Google OAuth / demo login
    ├── dashboard.html  # Application cards, stats, edit modal
    └── analytics.html  # Chart.js funnel, timeline, skills charts
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in:

```env
FLASK_SECRET_KEY=your-secret-key
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
ANTHROPIC_API_KEY=your-anthropic-api-key
```

### 3. Google OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an OAuth 2.0 Client ID (Web application)
3. Add `http://127.0.0.1:5000/auth/callback` as an authorized redirect URI
4. Enable the **Gmail API** in your project

### 4. Run

```bash
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

**Demo mode** (no credentials needed): omit `GOOGLE_CLIENT_ID` from `.env` and the app runs with sample data automatically.

## How the AI Agent Works

1. Searches Gmail for job-related emails using keyword filters on subjects
2. Filters out emails already tracked in the database
3. Sends new emails to **Claude Haiku** using the tool use API — Claude calls `save_job_application` for each job-related email, returning structured fields: company, role, status, applied date, interview date, notes, and skills
4. Results are upserted into the database with forward-only status progression

## Data Model

**JobApplication**

| Field | Type | Notes |
|---|---|---|
| company | string | required |
| role | string | job title |
| status | enum | applied / in_process / interview_scheduled / rejected / offer |
| applied_date | date | |
| interview_date | date | |
| notes | text | one-sentence AI summary |
| skills | JSON text | list of skills extracted from the email |
| gmail_message_id | string | used for deduplication |
