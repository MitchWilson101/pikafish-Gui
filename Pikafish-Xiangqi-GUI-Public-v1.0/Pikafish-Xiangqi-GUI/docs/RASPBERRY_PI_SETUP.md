# Raspberry Pi / Remote Pikafish Setup

This GUI does not install Pikafish automatically.

A typical setup is:

```text
Windows PC
  Pikafish Xiangqi GUI
        |
        | SSH
        v
Raspberry Pi / Linux PC
  Pikafish engine
```

## SSH

Make sure SSH is enabled on the remote Linux machine and that you can connect from Windows before using the GUI:

```powershell
ssh your-user@your-pi-hostname-or-ip
```

## Pikafish executable

Find the full path to the Pikafish executable on the remote machine. The GUI needs that full path in the connection settings.

Example only:

```text
/home/your-user/Pikafish/src/pikafish
```

Do not copy the example literally unless that is actually where your engine is installed.

## Test the engine manually

From Windows:

```powershell
ssh -T your-user@your-pi-hostname-or-ip /full/path/to/pikafish
```

If Pikafish starts, type:

```text
uci
```

You should receive a response ending with `uciok`.
