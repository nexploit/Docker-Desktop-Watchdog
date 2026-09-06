# Docker-Desktop-Watchdog
Watchdog for Docker-Desktop Stucks
# Docker Desktop Watchdog

A lightweight PowerShell watchdog for **Docker Desktop on Windows 10/11 with WSL2**.

The script checks whether Docker Desktop and its relevant Windows/WSL components are healthy. If the Docker Engine becomes unavailable, the watchdog can automatically reset the affected components and restart Docker Desktop.

## Features

* Checks the Docker Desktop service `com.docker.service`
* Detects the installed WSL service automatically:

  * `WslService`
  * `LxssManager` on older installations
* Checks relevant Windows services:

  * `vmcompute`
  * `hns`
* Checks the `docker-desktop` WSL distribution
* Verifies the Docker Engine using `docker info`
* Uses multiple health checks before triggering a restart
* Automatically shuts down WSL when recovery is required
* Restarts Docker Desktop automatically
* Supports forced recovery with `-ForceRepair`
* Writes status and recovery events to a log file
* Can be run automatically using Windows Task Scheduler

## Requirements

* Windows 10 or Windows 11
* Docker Desktop
* WSL2 backend
* Windows PowerShell 5.1 or newer
* Administrator privileges

Docker Desktop should normally be installed at:

```text
C:\Program Files\Docker\Docker\Docker Desktop.exe
```

## Installation

Create a directory for the watchdog:

```powershell
New-Item -ItemType Directory -Path "C:\Scripts" -Force
```

Copy the script into:

```text
C:\Scripts\Docker-Desktop-Watchdog-v2.ps1
```

The resulting structure should look like:

```text
C:\
└── Scripts\
    └── Docker-Desktop-Watchdog-v2.ps1
```

## Manual Usage

Open PowerShell **as Administrator**.

Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass

C:\Scripts\Docker-Desktop-Watchdog-v2.ps1
```

Alternatively:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Scripts\Docker-Desktop-Watchdog-v2.ps1"
```

## Health Checks

The watchdog checks several components before deciding whether Docker Desktop needs to be restarted.

### Docker Service

The script checks:

```text
com.docker.service
```

### WSL

Depending on the installed WSL version, the script automatically detects either:

```text
WslService
```

or:

```text
LxssManager
```

A stopped optional WSL service alone does **not** trigger a complete Docker restart.

### Windows Virtualization Services

The watchdog also checks:

```text
vmcompute
hns
```

These services are treated carefully to avoid unnecessary Docker restarts.

### Docker Engine

The most important health check is:

```powershell
docker info
```

By default, the watchdog performs up to **three attempts**.

The default delay between attempts is:

```text
5 seconds
```

Only if the Docker Engine remains unavailable does the watchdog start the recovery procedure.

This prevents unnecessary Docker Desktop restarts during temporary initialization delays.

## Automatic Recovery

If Docker Desktop is considered unhealthy, the watchdog performs the following recovery sequence:

```text
Docker health check failed
        │
        ▼
Stop Docker Desktop processes
        │
        ▼
wsl.exe --shutdown
        │
        ▼
Check/start required Windows services
        │
        ▼
Detect WslService / LxssManager
        │
        ▼
Start Docker Desktop
        │
        ▼
Wait for Docker Engine
        │
        ▼
docker info
        │
        ├── Success → Docker recovered
        │
        └── Failure → Write error to log
```

The default startup timeout is **90 seconds**.

## Force Recovery

A recovery can be triggered manually even if Docker currently appears healthy:

```powershell
C:\Scripts\Docker-Desktop-Watchdog-v2.ps1 -ForceRepair
```

This is useful when Docker Desktop appears frozen while some health checks still succeed.

## Configuration

Several values can be changed directly from the command line.

### Startup timeout

Example with a 120-second timeout:

```powershell
.\Docker-Desktop-Watchdog-v2.ps1 -DockerStartupTimeout 120
```

### Number of Docker Engine checks

```powershell
.\Docker-Desktop-Watchdog-v2.ps1 -EngineCheckRetries 5
```

### Delay between checks

```powershell
.\Docker-Desktop-Watchdog-v2.ps1 -EngineRetryDelaySeconds 10
```

Parameters can also be combined:

```powershell
.\Docker-Desktop-Watchdog-v2.ps1 `
    -DockerStartupTimeout 120 `
    -EngineCheckRetries 5 `
    -EngineRetryDelaySeconds 10
```

## Logging

Logs are written to:

```text
C:\ProgramData\DockerDesktopWatchdog\watchdog.log
```

Display the latest entries:

```powershell
Get-Content "C:\ProgramData\DockerDesktopWatchdog\watchdog.log" -Tail 50
```

Example:

```text
2026-09-06 20:30:01 [INFO] Docker Desktop Watchdog v2 gestartet.
2026-09-06 20:30:01 [OK] com.docker.service laeuft.
2026-09-06 20:30:01 [OK] vmcompute laeuft.
2026-09-06 20:30:01 [OK] hns laeuft.
2026-09-06 20:30:01 [INFO] Erkannter WSL-Dienst: WslService
2026-09-06 20:30:02 [OK] docker info erfolgreich (Versuch 1/3).
2026-09-06 20:30:02 [OK] Alle kritischen Docker-Komponenten arbeiten normal.
```

## Windows Task Scheduler

The watchdog can be executed periodically using Windows Task Scheduler.

Open Task Scheduler:

```text
taskschd.msc
```

Create a new task using **Create Task**, not "Create Basic Task".

### General

Recommended settings:

```text
Name:
Docker Desktop Watchdog

Run only when user is logged on:
Enabled

Run with highest privileges:
Enabled

Configure for:
Windows 10
```

Running in the logged-in user's session is recommended because Docker Desktop itself is a user application.

### Trigger

For example:

```text
Begin the task:
On a schedule

Repeat task every:
5 minutes

For a duration of:
Indefinitely
```

An additional **At log on** trigger can be configured.

### Action

Program:

```text
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
```

Arguments:

```text
-NoProfile -ExecutionPolicy Bypass -File "C:\Scripts\Docker-Desktop-Watchdog-v2.ps1"
```

Start in:

```text
C:\Scripts
```

### Task Settings

Recommended:

```text
Run task as soon as possible after a scheduled start is missed:
Enabled

If the task fails, restart every:
1 minute

Attempt to restart up to:
3 times

If the task is already running:
Do not start a new instance
```

## Testing the Scheduled Task

Before enabling periodic execution, test the exact command manually from an elevated PowerShell:

```powershell
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File "C:\Scripts\Docker-Desktop-Watchdog-v2.ps1"
```

Then check:

```powershell
Get-Content "C:\ProgramData\DockerDesktopWatchdog\watchdog.log" -Tail 30
```

## Exit Codes

The watchdog uses exit codes that can also be evaluated by monitoring systems or Windows Task Scheduler.

| Exit Code | Meaning                                 |
| --------: | --------------------------------------- |
|       `0` | Docker is healthy or recovery succeeded |
|      `10` | Administrator privileges are missing    |
|      `20` | Automatic Docker recovery failed        |

## Troubleshooting

### `LxssManager` cannot be started

Some WSL installations use:

```text
WslService
```

instead of:

```text
LxssManager
```

Version 2 of the watchdog automatically detects which service is available.

A stopped optional WSL service alone does not cause Docker Desktop to be restarted as long as the Docker Engine is healthy.

### Task Scheduler reports "Action failed to start"

Verify that the PowerShell executable exists:

```powershell
Test-Path "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
```

Verify the watchdog script:

```powershell
Test-Path "C:\Scripts\Docker-Desktop-Watchdog-v2.ps1"
```

Both commands should return:

```text
True
```

### Docker still does not start

Check the watchdog log:

```powershell
Get-Content "C:\ProgramData\DockerDesktopWatchdog\watchdog.log" -Tail 100
```

Docker Desktop logs can normally be found below:

```text
%LOCALAPPDATA%\Docker\log
```

You can also inspect the current WSL state:

```powershell
wsl --status
wsl -l -v
```

and test Docker directly:

```powershell
docker info
```

## Safety

The watchdog does **not** delete:

* Docker images
* containers
* volumes
* Docker Compose projects
* WSL distributions
* Docker configuration

The recovery procedure uses:

```powershell
wsl.exe --shutdown
```

This stops **all running WSL2 distributions**, not only Docker Desktop.

Therefore, other active WSL environments will also be stopped during an automatic Docker recovery.

Do not use the watchdog on systems where unrelated WSL workloads must run continuously without interruption.

## Repository Structure

A minimal repository can look like:

```text
docker-desktop-watchdog/
├── Docker-Desktop-Watchdog-v2.ps1
├── README.md
├── LICENSE
└── .gitignore
```

## License

This project can be distributed under the MIT License.

See:

```text
LICENSE
```

for details.
