# Desktop Automation Recorder

A production-grade desktop automation tool that records user actions (clicks, typing, scrolling, screenshots) and replays them on schedule with email notifications.

## Features

### 🎬 Recording
- **Full Desktop Recording**: Captures mouse clicks, keyboard input, scrolling, and more
- **Visual Context**: Screenshots captured around click points for reliable playback
- **Credential Protection**: Passwords are never recorded - only secure references stored
- **Manual Screenshots**: Take screenshots at any point during recording

### 🔐 Security
- **System Keychain Integration**: Credentials stored in Windows Credential Manager / macOS Keychain / Linux Secret Service
- **Encrypted Fallback**: AES-256 encryption with PBKDF2 key derivation
- **No Plaintext Passwords**: Credentials referenced by name, actual values fetched at playback time
- **Memory Protection**: Sensitive data cleared from memory after use

### ⏰ Scheduling
- **Flexible Scheduling**: Once, hourly, daily, weekly, monthly, or custom cron
- **Persistent Storage**: Schedules survive app restarts
- **Background Execution**: Runs in background without GUI

### 📧 Email Notifications
- **Automated Reports**: Email sent after each scheduled run
- **Screenshot Attachments**: Screenshots included in emails
- **Success/Failure Status**: Clear status reporting

### 🔄 Playback
- **Visual Verification**: Verifies screen state before clicking
- **Smart Retry**: Automatic retry on failure with configurable attempts
- **Speed Control**: Adjust playback speed
- **Abort Safety**: Move mouse to corner to abort

## Installation

### Prerequisites
- Python 3.10+
- pip

### Install

```bash
# Clone the repository
git clone <repo-url>
cd desktop-automation

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

### Platform-Specific Notes

**Linux:**
```bash
# Required for pynput
sudo apt-get install python3-tk python3-dev

# For system keyring
sudo apt-get install libsecret-1-dev
```

**macOS:**
- Grant accessibility permissions in System Preferences → Security & Privacy → Accessibility

**Windows:**
- No additional setup required

## Usage

### Start the Application

```bash
python run.py
```

### Recording a Workflow

1. Click **"⏺️ Start Recording"**
2. Enter a name for your recording
3. Optionally enter a starting URL
4. Perform your actions:
   - Navigate to websites
   - Click buttons and links
   - Fill in forms
   - Apply filters
   - Click **"📸 Screenshot"** to capture results
5. For password fields, click **"🔑 Mark Password"** BEFORE typing
6. Click **"⏹️ Stop Recording"** when done

### Adding Credentials

1. Go to the **🔐 Credentials** tab
2. Click **"➕ Add"**
3. Enter:
   - Name (e.g., "Company Portal")
   - Username
   - Password
   - URL (optional)
4. Click OK

### Scheduling

1. Select a recording in the **📹 Recordings** tab
2. Click **"📅 Schedule"**
3. Configure:
   - Schedule name
   - Frequency (daily, weekly, etc.)
   - Email recipients
4. Click OK

### Email Configuration

1. Go to **⚙️ Settings** tab
2. Click **"Configure"** under Email
3. Enter SMTP details:
   - For Gmail: Use App Password (not regular password)
   - Host: smtp.gmail.com
   - Port: 587

## Architecture

```
desktop-automation/
├── src/
│   ├── __init__.py      # Package exports
│   ├── recorder.py      # Action recording engine
│   ├── playback.py      # Playback engine with visual verification
│   ├── credentials.py   # Secure credential storage
│   ├── scheduler.py     # APScheduler-based scheduling
│   ├── database.py      # SQLAlchemy models
│   └── gui.py           # PyQt6 interface
├── requirements.txt
├── run.py               # Entry point
└── README.md
```

## Security Model

### Credential Storage
1. **Primary**: System keyring (most secure)
2. **Fallback**: Encrypted file with master password

### During Recording
- When you click "Mark Password", the next input is NOT recorded
- Instead, a reference to the credential is stored: `credential_name:password`

### During Playback
- The reference is resolved to the actual password from secure storage
- Password is typed, then immediately cleared from memory

### What's Stored in Recordings
```json
{
  "action_type": "credential_input",
  "credential_name": "Company Portal",
  "credential_field": "password"
}
```

The actual password is NEVER stored in recordings.

## Troubleshooting

### Recording Not Working
- **Linux**: Ensure you have permissions for input capture
- **macOS**: Grant Accessibility permissions
- **All**: Run as administrator/sudo if needed

### Playback Fails
- Ensure the target application is in the same position
- Enable "Verify screenshots" for more reliable clicking
- Increase retry count for slow applications

### Email Not Sending
- Check SMTP credentials
- For Gmail: Enable "Less secure app access" or use App Password
- Check firewall settings

## API Reference

### ActionType Enum
```python
MOUSE_CLICK      # Single left click
MOUSE_DOUBLE_CLICK
MOUSE_RIGHT_CLICK
MOUSE_SCROLL
KEY_PRESS        # Single key
KEY_TYPE         # Text input
CREDENTIAL_INPUT # Secure credential reference
SCREENSHOT       # Full screen capture
WAIT             # Explicit delay
OPEN_URL         # Open in browser
```

### Recording Class
```python
Recording(
    id: str,
    name: str,
    description: str,
    url: Optional[str],
    actions: List[RecordedAction],
    created_at: datetime,
    updated_at: datetime
)
```

### Schedule Class
```python
Schedule(
    id: str,
    name: str,
    recording_id: str,
    frequency: ScheduleFrequency,
    email_recipients: List[str],
    is_active: bool
)
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

## License

MIT License - see LICENSE file
