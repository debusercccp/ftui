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
cd ftui
pip install -e . --break-system-packages
```

## Avvio

```bash
ftui
```

Se il comando non viene trovato, aggiungi `~/.local/bin` al PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

## Tasti

| Tasto  | Azione                              |
|--------|-------------------------------------|
| F2     | Nuova connessione                   |
| F3     | Bookmark salvati                    |
| F5     | Trasferisci file selezionato        |
| F6     | NAS Sync (FTP → home locale)        |
| F7     | Crea directory                      |
| F8     | Elimina                             |
| F9     | Rinomina                            |
| Tab    | Cambia pannello                     |
| Enter  | Entra nella directory               |
| Q      | Esci                                |

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

## NAS Sync (F6)

Sincronizza una o più directory dal NAS FTP verso la home locale.

- Se il file **non esiste in locale** viene scaricato automaticamente
- Se il **NAS è più recente** viene chiesto cosa fare (NAS / locale / salta)
- Se il **locale è più recente** viene lasciato intatto

Se sei già connesso via F2 a un server FTP, il modal riusa la connessione esistente.
Altrimenti inserisci host, porta, utente e password direttamente nel modal.

## Struttura

```
ftui/
├── ftui/
│   ├── __init__.py
│   ├── app.py          -- FtuiApp: entry point, bindings, azioni
│   ├── styles.py       -- CSS centralizzato
│   ├── modals.py       -- ConnectModal, BookmarksModal, InputModal, ConfirmModal
│   ├── pane.py         -- FilePane: pannello locale/remoto
│   ├── nas_sync.py     -- NasSyncModal, ConflictModal
│   ├── protocols.py    -- Astrazione FTP/FTPS/SFTP/SCP
│   └── bookmarks.py    -- Salvataggio connessioni (~/.config/ftui/bookmarks.json)
├── pyproject.toml
└── README.md
```
