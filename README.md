# ftui

Dual-pane TUI file transfer client, versione leggera basata su prompt_toolkit + rich.
Pensata per Raspberry Pi e terminali lenti. Zero overhead rispetto a Textual.

```
  ftui  --  FTP / FTPS / SFTP / SCP
+----------------------+|+----------------------+
 Local                  |  Remote  [SFTP]
 /home/noya             |  /var/www/html
+-----------------------++-----------------------+
 Name              Size  |  Name              Size
>DIR  Documents   <DIR>  |  DIR  assets      <DIR>
 DIR  Downloads   <DIR>  |      index.html   4.2 KB
     report.pdf  1.2 MB  |      app.js        12 KB
     notes.txt   3.1 KB  |      style.css    8.1 KB
+--------------------------------------------------+
 DN  report.pdf  120/1200 KB  [██████░░░░░░░░] 10%
 F2 Connect  F3 Bookmarks  F5 Transfer  F7 Mkdir  F8 Delete  F9 Rename  Tab Switch  Q Quit
```

## Dipendenze

```bash
pip install -e . --break-system-packages

```

## Avvio

```bash
python3 -m ftui.app
```

## Tasti

| Tasto      | Azione                                   |
|------------|------------------------------------------|
| F2         | Nuova connessione                        |
| F3         | Bookmark salvati                         |
| F5         | Trasferisci file o cartella selezionata  |
| F7         | Crea directory                           |
| F8         | Elimina (chiede conferma con 'yes')      |
| F9         | Rinomina                                 |
| Tab        | Cambia pannello                          |
| Enter      | Entra nella directory                    |
| PgUp/PgDn  | Scorrimento veloce                       |
| Esc        | Chiudi modal                             |
| Q          | Esci                                     |

## Differenze rispetto a ftui (Textual)

- Nessun event loop asincrono — prompt_toolkit usa un loop sincrono molto piu leggero
- Redraws solo su cambio di stato, mai su timer
- Mouse disabilitato di default (troppo lento su Pi via SSH)
- Conferma delete testuale ('yes') invece di dialog grafico
- Modal connessione navigabile con Tab/Su/Giu
- Bookmarks: premi F3 e usa Su/Giu + Enter per selezionare

## Struttura
```
/home/noya/progetti/ftui/ftui/
├── app.py
├── bookmarks.py
├── __init__.py
├── protocols.py
└── __pycache__
    ├── app.cpython-313.pyc
    ├── bookmarks.cpython-313.pyc
    ├── __init__.cpython-313.pyc
    └── protocols.cpython-313.pyc
``````
ftui/
├── app.py
├── bookmarks.py
├── __init__.py
├── modals.py
├── pane.py
├── protocols.py
├── __pycache__
│   ├── app.cpython-313.pyc
│   ├── bookmarks.cpython-313.pyc
│   ├── __init__.cpython-313.pyc
│   └── protocols.cpython-313.pyc
└── styles.py
```
```
ftui/
├── ftui/
│   ├── __init__.py
│   ├── app.py          -- TUI principale (prompt_toolkit)
│   ├── protocols.py    -- FTP/FTPS/SFTP/SCP 
│   └── bookmarks.py    -- Salvataggio connessioni
├── pyproject.toml
└── README.md
```

## Bookmark

Salvati in ~/.config/ftui/bookmarks.json.
