# Pikafish Xiangqi GUI

A Windows desktop Xiangqi (Chinese chess) interface and training tool built with **Python + PySide6**.

The GUI connects over SSH to a separate **Pikafish** engine, typically running on a Raspberry Pi or another Linux computer.

> **Pikafish itself is not included in this repository.** Install it separately and enter your own SSH connection details in the app.

## Features

- Xiangqi board with Chinese and European-style pieces
- Remote Pikafish connection over SSH
- Engine move and hint
- Adjustable analysis time
- Best-line arrows
- Top-3 / MultiPV analysis
- PGN load, replay, save and annotated save
- Evaluation graph with clickable positions
- Automatic game review
- Best / Good / Inaccuracy / Mistake / Blunder classifications
- Separate Red and Black review totals
- Previous / Next mistake navigation
- Post-game coaching
- Progressive coaching hints
- Retry Mistakes training mode
- Easy / Medium / Hard play-against-Pikafish modes
- Copy and load FEN
- Save interesting positions
- Opening recognition
- Small built-in opening explorer / trainer
- Game statistics and improvement tracking
- Move sound

## Screenshots

Add screenshots to the `screenshots/` folder and reference them here, for example:

```markdown
![Main window](screenshots/main-window.png)
```

## Requirements

### Windows computer

- Windows 10 or Windows 11
- Python 3.11 or 3.12 recommended
- Windows OpenSSH client
- Network access to the computer running Pikafish

### Remote Pikafish computer

Pikafish must already be installed and executable on the remote computer.

You will need:

- hostname or IP address
- SSH username
- full path to the Pikafish executable
- SSH key or another SSH authentication method supported by your Windows OpenSSH setup

## Install from source

```powershell
git clone https://github.com/YOUR-USERNAME/Pikafish-Xiangqi-GUI.git
cd Pikafish-Xiangqi-GUI
py -3 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python pikafish_xiangqi.py
```

Replace `YOUR-USERNAME` with your GitHub username after you create the repository.

## First connection

1. Start the program.
2. Click **Connect**.
3. Enter your own:
   - Host / IP
   - SSH port (normally `22`)
   - Username
   - Pikafish executable path
   - Optional SSH key
4. Save/connect.

Connection settings are stored only in the user's home folder in:

```text
.pikafish_gui.json
```

That file is ignored by Git and should never be committed.

## Build a Windows EXE

A build script is included.

1. Keep the repository in a short Windows path if possible, for example:
   `C:\PikafishGUI`
2. Double-click:
   `build_windows_exe.bat`
3. The finished application will appear at:
   `dist\Pikafish Xiangqi.exe`

The EXE is built with PyInstaller's `--windowed` option, so it launches without a PowerShell/console window.

## Local data

The application stores local data in the user's home directory:

```text
.pikafish_gui.json
.pikafish_positions.json
.pikafish_game_stats.json
```

These files are intentionally excluded from Git.

## Security / privacy

The public source contains **no personal SSH username, IP address, private key path, password, or machine-specific Pikafish path**.

Do not commit:

- `.pikafish_gui.json`
- SSH private keys
- passwords
- local IP addresses if you consider them sensitive
- personal PGN files unless you intend to share them

## Pikafish

This GUI is a separate front end and does not bundle Pikafish.

Pikafish project:
https://github.com/official-pikafish/Pikafish

Please follow the Pikafish project's own licence and distribution requirements if you redistribute the engine itself.

## License

This GUI source is released under the MIT License. See [`LICENSE`](LICENSE).

## Status

**Public v1.0**

The project began as a personal Raspberry Pi + Pikafish interface and has grown into a Xiangqi analysis and training application.
