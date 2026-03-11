# ftui — dual-pane TUI file transfer client

Un'alternativa moderna a FileZilla, completamente da terminale.

```
┌─────────────────────────┬─────────────────────────┐
│  💻 Local               │  🌐 Remote [SFTP]        │
│  /home/rocco            │  /var/www/html           │
├─────────────────────────┼─────────────────────────┤
│  📁 ..                  │  📁 ..                   │
│  📁 Documents           │  📁 assets               │
│  📁 Downloads           │  📁 css                  │
│▶ 📄 report.pdf  1.2 MB  │  📄 index.html   4.2 KB  │
│  📄 notes.txt   3.1 KB  │  📄 app.js       12 KB   │
└─────────────────────────┴─────────────────────────┘
 ↑ report.pdf  ████████████████░░░░  73%  1.2 MB/s
```

## Installazione

```bash
# 1. Clona o copia il progetto
cd ftui/

# 2. Installa in modalità sviluppo
pip install -e .

# Oppure senza installare, esegui direttamente:
python -m ftui.app
```

## Utilizzo

```bash
ftui
```

Oppure:
```bash
python -m ftui.app
```

## Scorciatoie da tastiera

| Tasto | Azione |
|-------|--------|
| `F2`  | Nuova connessione |
| `F3`  | Apri bookmark salvati |
| `F5`  | Trasferisci file selezionato |
| `F7`  | Crea directory |
| `F8`  | Elimina file/directory |
| `F9`  | Rinomina |
| `Tab` | Cambia pannello attivo |
| `Enter` | Entra nella directory |
| `Q`   | Esci |

## Protocolli supportati

| Protocollo | Porta default | Note |
|-----------|--------------|-------|
| SFTP      | 22           | Raccomandato, usa SSH |
| SCP       | 22           | Via SSH, fallback a SFTP per listing |
| FTP       | 21           | Non cifrato |
| FTPS      | 21           | FTP con TLS |

## Bookmark

Quando ti connetti puoi salvare la connessione con un nome.  
I bookmark vengono salvati in `~/.config/ftui/bookmarks.json`.

## Dipendenze

```
textual>=0.53    # TUI framework
paramiko>=3.4   # SSH/SFTP
rich>=13        # UI components
scp>=0.15       # (opzionale) trasferimento SCP nativo
```

## Struttura progetto

```
ftui/
├── ftui/
│   ├── __init__.py
│   ├── app.py          ← TUI principale (Textual)
│   ├── protocols.py    ← Astrazione FTP/FTPS/SFTP/SCP
│   └── bookmarks.py    ← Salvataggio connessioni
├── pyproject.toml
└── README.md
```
