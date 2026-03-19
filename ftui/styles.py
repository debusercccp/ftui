"""ftui/styles.py — CSS centralizzato per tutta l'app."""

CSS = """
Screen {
    background: #0d1117;
}
Header {
    background: #161b22;
    color: #6ea8fe;
    text-style: bold;
}
Footer {
    background: #161b22;
    color: #6e7681;
}
#layout {
    layout: horizontal;
    height: 1fr;
}
.pane {
    width: 1fr;
    border: solid #21262d;
    margin: 0 1;
}
.pane:focus-within {
    border: solid #388bfd;
}
.pane-title {
    background: #161b22;
    color: #e6edf3;
    text-align: center;
    padding: 0 1;
    text-style: bold;
}
.pane-path {
    background: #0d1117;
    color: #388bfd;
    padding: 0 1;
}
DataTable {
    height: 1fr;
    background: #0d1117;
}
DataTable > .datatable--header {
    background: #161b22;
    color: #6e7681;
    text-style: bold;
}
DataTable > .datatable--cursor {
    background: #1f6feb;
    color: #ffffff;
}
DataTable > .datatable--hover {
    background: #1c2128;
}
DataTable > .datatable--zebra-stripe {
    background: #0d1117;
}
#transfer-bar {
    height: 3;
    background: #161b22;
    border-top: solid #21262d;
    padding: 0 2;
    display: none;
    layout: horizontal;
    align: left middle;
}
#transfer-bar.visible {
    display: block;
}
#transfer-label {
    color: #388bfd;
    width: auto;
    margin-right: 2;
}
ProgressBar {
    width: 1fr;
}
.status-bar {
    height: 1;
    background: #161b22;
    color: #6e7681;
    padding: 0 2;
}
.modal-screen {
    align: center middle;
    background: rgba(0,0,0,0.75);
}
.modal-box {
    background: #161b22;
    border: solid #30363d;
    padding: 2 4;
    width: 72;
    height: auto;
}
.modal-title {
    text-style: bold;
    color: #6ea8fe;
    margin-bottom: 1;
}
.field-label {
    color: #6e7681;
    margin-top: 1;
}
Input {
    background: #0d1117;
    border: solid #30363d;
    color: #e6edf3;
}
Input:focus {
    border: solid #388bfd;
}
Select {
    background: #0d1117;
    border: solid #30363d;
    color: #e6edf3;
}
.btn-row {
    layout: horizontal;
    height: auto;
    margin-top: 2;
    align: right middle;
}
Button {
    margin-left: 1;
    background: #21262d;
    color: #e6edf3;
    border: solid #30363d;
}
Button:hover {
    background: #30363d;
}
Button.primary {
    background: #1f6feb;
    color: #ffffff;
    border: solid #1f6feb;
}
Button.primary:hover {
    background: #388bfd;
    border: solid #388bfd;
}
Button.danger {
    background: #b91c1c;
    color: #ffffff;
    border: solid #b91c1c;
}
Button.danger:hover {
    background: #dc2626;
}
.error-label {
    color: #f85149;
    margin-top: 1;
}
#sync-log {
    height: 14;
    background: #0d1117;
    border: solid #21262d;
    overflow-y: scroll;
    padding: 0 1;
    margin-top: 1;
}
.sync-ok   { color: #3fb950; }
.sync-warn { color: #d29922; }
.sync-err  { color: #f85149; }
.sync-info { color: #388bfd; }
"""
