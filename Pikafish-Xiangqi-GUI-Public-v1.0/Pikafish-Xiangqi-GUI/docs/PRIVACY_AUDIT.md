# Public-source privacy audit

Automated scan of `pikafish_xiangqi.py`:

- PASS — private IPv4 192.168.x.x
- PASS — Windows user path
- PASS — specific former SSH username
- PASS — specific /home/<user>/ path
- PASS — raspberrypi.local default

Public connection defaults:

- Host: blank
- Username: blank
- Engine path: blank
- SSH key: blank
- Password: never stored by the GUI
