# Codex Auto Approve

A small Windows/Tkinter utility that invokes the exact **Allow once** button in
Codex mode or the **Allow** button beside **Skip** in VS Code Copilot mode. It uses
Windows UI Automation, so it does not move the pointer, click screen coordinates,
or steal focus from another app.

## Setup

From PowerShell in this folder:

```powershell
py -m pip install -r requirements.txt
py .\codex_auto_approve.py
```

After setup, you can also double-click `run.bat`.

Press **Start** to monitor for prompts and **Stop** to stop approving them. Closing
the utility also stops the monitor.

Choose **Codex** mode for `Allow once` prompts, or **Copilot** mode for the blue
`Allow` button shown beside `Skip` in VS Code.

## Important

This removes the chance to review each Codex command before it runs. Use it only
while you trust the task Codex is performing, and press **Stop** before starting
work where you want to inspect each requested command.
