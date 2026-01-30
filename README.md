# AI Browser Automation

A production-grade desktop automation tool that records user actions (clicks, typing, scrolling, screenshots) and replays them on schedule with AI-powered audit analysis, HTML reporting, and email notifications.

## Features

### Recording
- **Full Desktop Recording** — captures mouse clicks, keyboard input, scrolling, and drag actions
- **Visual Context** — screenshots captured around click points for reliable playback verification
- **Credential Protection** — passwords are never recorded; only secure references are stored
- **Manual Screenshots** — take screenshots at any point during recording via floating toolbar
- **Floating Toolbar** — draggable always-on-top toolbar with live action counter, screenshot button, and password marker

### AI Audit (Anthropic Claude)
- **Automated Screenshot Analysis** — all playback screenshots sent to Claude in a single API call for visual QA review
- **Executive Summary Generation** — AI-generated summary of the entire workflow execution with findings and verdict
- **HTML Report Generation** — self-contained dark-themed HTML report with inline screenshots, timing data, and AI commentary
- **Email Delivery** — full HTML report embedded in email body with the report file attached
- **Cost Tracking** — tracks API call count and estimated cost per audit

### Security
- **System Keychain Integration** — Windows Credential Manager, macOS Keychain, or Linux Secret Service
- **Encrypted Fallback** — AES-256 encryption with PBKDF2 key derivation (480,000 iterations per OWASP guidelines)
- **No Plaintext Passwords** — credentials referenced by name; actual values fetched only at playback time
- **Memory Protection** — sensitive data zeroed out after use
- **Secure Export/Import** — credential export uses salt-prefixed Fernet encryption

### Scheduling
- **Flexible Scheduling** — once, hourly, daily, weekly, monthly, or custom cron expressions
- **Persistent Storage** — schedules and job state survive app restarts (SQLAlchemy-backed APScheduler)
- **Background Execution** — runs without GUI interaction

### Playback
- **Visual Verification** — verifies screen state before clicking using image matching
- **Smart Retry** — automatic retry on failure with configurable attempts
- **Speed Control** — adjust playback speed multiplier
- **Page Load Detection** — screen-hash-based stability detection to measure page load times
- **Abort Safety** — PyAutoGUI failsafe (move mouse to corner to abort)

### Email Notifications
- **Automated Reports** — email sent after each scheduled run or AI audit
- **Screenshot Attachments** — up to 5 screenshots attached
- **SMTP/TLS Support** — works with Gmail, Outlook, and custom SMTP servers

## Installation

### Prerequisites
- Python 3.9+
- pip

### Setup

```bash
# Clone the repository
git clone https://github.com/davisge2/ai_browser_automation.git
cd ai_browser_automation

# Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# Install dependencies
pip install -r requirements.txt
```

### Platform Notes

**Windows** — no additional setup required.

**Linux:**
```bash
sudo apt-get install python3-tk python3-dev libsecret-1-dev
```

**macOS** — grant Accessibility permissions in System Preferences > Security & Privacy > Accessibility.

## Usage

### Start the Application

```bash
python run.py
python run.py --verbose   # debug logging
python run.py --check     # verify dependencies
```

### Recording a Workflow

1. Click **Start Recording**
2. Enter a name, optional starting URL, and optional email recipients
3. A floating toolbar appears at the top of the screen — the main window hides
4. Perform your actions (navigate, click, type, scroll)
5. Click **Screenshot** on the toolbar to capture the current screen state
6. Click **Password** on the toolbar before typing credentials (stores a secure reference, not keystrokes)
7. Click **Stop** when done
8. Optionally fill in the audit context dialog (purpose and verification goal for AI analysis)

### Playing Back a Recording

1. Select a recording in the **Recordings** tab
2. Click **Play** — the tool replays all actions with timing
3. If AI Audit is enabled in Settings, an audit report is automatically generated and opened in the browser
4. If email recipients were configured, the report is emailed

### Scheduling

1. Select a recording, click **Schedule**
2. Choose frequency and enter email recipients
3. The scheduler runs in the background and sends reports after each execution

### AI Audit Setup

1. Go to **Settings** tab
2. Check "Enable AI audit after playback"
3. Enter your Anthropic API key and save
4. Optionally configure a custom report output folder

### Credential Management

1. Go to **Credentials** tab, click **Add**
2. Enter name, username, password, and optional URL
3. Set a master password in **Settings** for encrypted file fallback

## Project Structure

```
ai_browser_automation/
├── run.py               # Entry point (GUI launcher + dependency checker)
├── recorder.py          # Action recording engine (pynput mouse/keyboard listeners)
├── playback.py          # Playback engine with visual verification and retry
├── ai_engine.py         # Anthropic Claude integration for screenshot analysis
├── report_generator.py  # Self-contained HTML audit report generator
├── credentials.py       # Secure credential storage (keyring + encrypted file)
├── scheduler.py         # APScheduler-based scheduling with email notifications
├── database.py          # SQLAlchemy models (recordings, schedules, runs, settings)
├── gui.py               # PyQt6 GUI (main window, dialogs, floating toolbar)
├── page_monitor.py      # Page load timing and window title monitoring
├── requirements.txt     # Python dependencies
└── .gitignore
```

## Security Model

### Credential Flow

```
Recording:  User clicks "Password" → types password → stored as {"credential_name": "X", "credential_field": "password"}
Playback:   Reference resolved → actual password fetched from keyring → typed → cleared from memory
```

The actual password is **never** stored in recording files.

### Storage Hierarchy
1. **System keyring** (primary) — OS-level credential storage
2. **Encrypted file** (fallback) — AES-256-CBC via Fernet, key derived with PBKDF2-HMAC-SHA256 (480k iterations)

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Recording not capturing input | Run as administrator; on macOS grant Accessibility permissions |
| Playback clicks wrong location | Enable visual verification; ensure target app is in same screen position |
| Email not sending | Verify SMTP credentials; for Gmail use an App Password |
| AI audit fails | Check API key is valid; ensure screenshots were captured during playback |

## License

MIT License
