# ftui

Dual-pane TUI file transfer client, alternativa a FileZilla da terminale.
Supporta FTP, FTPS, SFTP e SCP.

```
+---------------------------+---------------------------+
|  Local                    |  Remote [SFTP]            |
|  /home/noya               |  /var/www/html            |
+---------------------------+---------------------------+
|  ..                       |  ..                       |
|  Documents/               |  assets/                  |
|  Downloads/               |  css/                     |
|> report.pdf       1.2 MB  |  index.html       4.2 KB  |
|  notes.txt        3.1 KB  |  app.js            12 KB  |
+---------------------------+---------------------------+
  Uploading report.pdf  ████████████░░░░  73%  1.2 MB/s
```

## Installazione

```bash
pip install textual paramiko rich --break-system-packages
```

## Avvio

```bash
python -m ftui.app
```

## Tasti

| Tasto  | Azione                        |
|--------|-------------------------------|
| F2     | Nuova connessione             |
| F3     | Bookmark salvati              |
| F5     | Trasferisci file selezionato  |
| F7     | Crea directory                |
| F8     | Elimina                       |
| F9     | Rinomina                      |
| Tab    | Cambia pannello               |
| Enter  | Entra nella directory         |
| Q      | Esci                          |

## Protocolli

| Protocollo | Porta default | Note                               |
|------------|---------------|------------------------------------|
| SFTP       | 22            | Raccomandato, cifrato via SSH      |
| SCP        | 22            | Via SSH, fallback SFTP per listing |
| FTP        | 21            | Non cifrato                        |
| FTPS       | 21            | FTP con TLS                        |

## Bookmark

Quando ti connetti puoi salvare la connessione con un nome.
I bookmark vengono salvati in `~/.config/ftui/bookmarks.json`.

## Struttura

```
ftui/
├── ftui/
│   ├── __init__.py
│   ├── app.py          -- TUI principale (Textual)
│   ├── protocols.py    -- Astrazione FTP/FTPS/SFTP/SCP
│   └── bookmarks.py    -- Salvataggio connessioni
├── pyproject.toml
└── README.md
```
