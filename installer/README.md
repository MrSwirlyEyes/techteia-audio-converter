# Building the Windows Installer

## Who does what

| Person | What they do |
|--------|-------------|
| **Developer (you)** | Run `build.bat` once on your Windows machine → get `Setup.exe` |
| **Grandma** | Double-click `Setup.exe` → click Next → Next → Install → Done |

Grandma never needs Python, pip, FFmpeg, or any of this. She just gets the `Setup.exe`.

---

## Developer setup (one-time)

### 1 — Run build.bat

Double-click `installer/build.bat`.

It will automatically:
1. Detect Python — and **install it silently** if not found (via winget or direct download)
2. Install `pyinstaller` and `customtkinter` via pip
3. **Download FFmpeg** from GitHub if not already present (no manual steps needed)
4. Bundle the app + FFmpeg into a self-contained folder under `dist/`
5. If Inno Setup is already installed, compile the installer automatically

> First run takes ~2–5 minutes. Subsequent runs are faster.

### 3 — Build the installer (if not done automatically)

If Inno Setup wasn't installed during step 2:

1. Install Inno Setup 6 from https://jrsoftware.org/isinfo.php
2. Re-run `build.bat` — it will now compile automatically

Or manually: open `installer/techteia.iss` in Inno Setup → press **Ctrl+F9**.

### 4 — Ship it

The finished installer is at:
```
installer/TechteiaAudioConverter_Setup_v1.0.4.exe
```

Send this single file to grandma. That's it.

---

## What the installer gives grandma

- One-click install with a friendly Next → Next → Install wizard
- Start Menu shortcut
- Desktop shortcut (optional, offered during install)
- "Launch now" option at the end
- Clean uninstall via Windows Settings → Apps

## Optional: App icon

1. Create or obtain a 256×256 `.ico` file
2. Save it as `installer/icon.ico`
3. In `build.bat`, add `--icon "%~dp0icon.ico" ^` to the PyInstaller command
4. In `techteia.iss` under `[Setup]`, add `SetupIconFile=icon.ico`
