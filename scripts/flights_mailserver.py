#!/usr/bin/env python3
"""A tiny mail server on this Mac for testing the Correspondent without a real account.

IMAP on 127.0.0.1:1143 (plain, no TLS) serves the .eml files in a folder as INBOX, unread. SMTP on 127.0.0.1:1025
(plain) accepts anything and writes each message it receives to <folder>/outbox/. Both speak just enough of the
protocols for LocalBook's client: LOGIN with any user and password, SELECT INBOX, UID SEARCH, UID FETCH, UID STORE,
UID COPY / UID MOVE, CREATE, LIST, EXPUNGE, LOGOUT; EHLO, AUTH PLAIN/LOGIN, MAIL FROM, RCPT TO, DATA, QUIT.

    python3 scripts/flights_mailserver.py testdata/flights/mail

Stop it with Ctrl-C. Flags: --imap-port, --smtp-port. Nothing here is real mail.
"""
import argparse
import email.utils
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

LOCK = threading.RLock()


class Store:
    def __init__(self, folder: Path):
        self.folder = folder
        self.outbox = folder / "outbox"
        self.outbox.mkdir(exist_ok=True)
        self.messages = {}  # uid -> dict(data, flags, mailbox)
        self.mailboxes = {"INBOX"}
        for uid, path in enumerate(sorted(folder.glob("*.eml")), start=1):
            self.messages[uid] = {"data": path.read_bytes(), "flags": set(), "mailbox": "INBOX", "name": path.name}
        self.next_uid = len(self.messages) + 1

    def in_box(self, mailbox):
        return {u: m for u, m in self.messages.items() if m["mailbox"] == mailbox}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class IMAPHandler(socketserver.StreamRequestHandler):
    store: Store = None  # set by main

    def send(self, line: str):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self.selected = None
        self.send("* OK [CAPABILITY IMAP4rev1 UIDPLUS MOVE] LocalBook test IMAP ready")
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\r\n")
            # Literal arguments ({n}\r\n...) for LOGIN passwords: read and splice.
            while line.endswith("}") and "{" in line:
                spec = line[line.rindex("{") + 1:-1]
                n = int(spec.rstrip("+"))
                if not spec.endswith("+"):
                    self.send("+ go ahead")
                lit = self.rfile.read(n).decode(errors="replace")
                rest = self.rfile.readline().decode(errors="replace").rstrip("\r\n")
                line = line[:line.rindex("{")] + '"' + lit.replace('"', '\\"') + '"' + rest
            parts = line.split(" ", 2)
            if len(parts) < 2:
                continue
            tag, cmd = parts[0], parts[1].upper()
            args = parts[2] if len(parts) > 2 else ""
            if cmd == "UID" and args:
                sub, _, args = args.partition(" ")
                cmd = "UID " + sub.upper()
            log("IMAP", cmd, args[:60])
            handler = getattr(self, "cmd_" + cmd.replace(" ", "_").lower(), None)
            if handler is None:
                self.send(f"{tag} BAD unknown command {cmd}")
                continue
            try:
                if handler(tag, args) == "bye":
                    return
            except Exception as e:  # noqa: BLE001
                self.send(f"{tag} NO {e}")

    def cmd_capability(self, tag, args):
        self.send("* CAPABILITY IMAP4rev1 UIDPLUS MOVE")
        self.send(f"{tag} OK done")

    def cmd_noop(self, tag, args):
        self.send(f"{tag} OK done")

    def cmd_login(self, tag, args):
        self.send(f"{tag} OK [CAPABILITY IMAP4rev1 UIDPLUS MOVE] logged in")

    def cmd_logout(self, tag, args):
        self.send("* BYE")
        self.send(f"{tag} OK bye")
        return "bye"

    def cmd_list(self, tag, args):
        with LOCK:
            for name in sorted(self.store.mailboxes):
                self.send(f'* LIST (\\HasNoChildren) "/" "{name}"')
        self.send(f"{tag} OK done")

    def cmd_create(self, tag, args):
        with LOCK:
            self.store.mailboxes.add(args.strip().strip('"'))
        self.send(f"{tag} OK created")

    def cmd_select(self, tag, args):
        name = args.strip().strip('"')
        with LOCK:
            if name not in self.store.mailboxes:
                self.send(f"{tag} NO no such mailbox")
                return
            box = self.store.in_box(name)
            self.selected = name
            self.send(f"* {len(box)} EXISTS")
            self.send("* 0 RECENT")
            self.send("* FLAGS (\\Seen \\Deleted)")
            self.send(f"* OK [UIDVALIDITY 1] ok")
            self.send(f"* OK [UIDNEXT {self.store.next_uid}] ok")
        self.send(f"{tag} OK [READ-WRITE] selected")

    def _uids(self, spec):
        with LOCK:
            box = self.store.in_box(self.selected or "INBOX")
        out = []
        for piece in spec.split(","):
            if ":" in piece:
                a, b = piece.split(":")
                lo = int(a)
                hi = max(box) if b == "*" or not box else int(b)
                if not box:
                    continue
                out += [u for u in box if lo <= u <= hi]
            elif piece == "*":
                if box:
                    out.append(max(box))
            else:
                u = int(piece)
                if u in box:
                    out.append(u)
        return sorted(set(out))

    def cmd_uid_search(self, tag, args):
        words = args.upper().split()
        with LOCK:
            box = self.store.in_box(self.selected or "INBOX")
        uids = sorted(box)
        if "UNSEEN" in words:
            uids = [u for u in uids if "\\Seen" not in box[u]["flags"]]
        if "UID" in words:
            spec = words[words.index("UID") + 1]
            uids = [u for u in uids if u in self._uids(spec)]
        self.send("* SEARCH" + ("".join(f" {u}" for u in uids)))
        self.send(f"{tag} OK done")

    def cmd_uid_fetch(self, tag, args):
        spec, _, what = args.partition(" ")
        for u in self._uids(spec):
            with LOCK:
                m = self.store.messages[u]
            data = m["data"]
            self.wfile.write(f"* {u} FETCH (UID {u} BODY[] {{{len(data)}}}\r\n".encode())
            self.wfile.write(data)
            self.wfile.write(b")\r\n")
        self.send(f"{tag} OK fetched")

    def cmd_uid_store(self, tag, args):
        spec, _, rest = args.partition(" ")
        flags = {f.strip("()") for f in rest.split()[1:]}
        with LOCK:
            for u in self._uids(spec):
                if rest.startswith("+"):
                    self.store.messages[u]["flags"] |= flags
                elif rest.startswith("-"):
                    self.store.messages[u]["flags"] -= flags
                else:
                    self.store.messages[u]["flags"] = flags
                log("   flags of", self.store.messages[u]["name"], "->", sorted(self.store.messages[u]["flags"]))
        self.send(f"{tag} OK stored")

    def _move(self, spec, dest, copy_only):
        dest = dest.strip().strip('"')
        with LOCK:
            if dest not in self.store.mailboxes:
                raise ValueError("no such mailbox")
            for u in self._uids(spec):
                if copy_only:
                    nu = self.store.next_uid
                    self.store.next_uid += 1
                    self.store.messages[nu] = dict(self.store.messages[u], mailbox=dest, flags=set())
                else:
                    self.store.messages[u]["mailbox"] = dest
                log("   ", self.store.messages[u]["name"], "->", dest)

    def cmd_uid_copy(self, tag, args):
        spec, _, dest = args.partition(" ")
        self._move(spec, dest, True)
        self.send(f"{tag} OK copied")

    def cmd_uid_move(self, tag, args):
        spec, _, dest = args.partition(" ")
        self._move(spec, dest, False)
        self.send(f"{tag} OK moved")

    def cmd_expunge(self, tag, args):
        with LOCK:
            gone = [u for u, m in self.store.messages.items() if m["mailbox"] == (self.selected or "INBOX") and "\\Deleted" in m["flags"]]
            for u in gone:
                log("   expunged", self.store.messages[u]["name"])
                del self.store.messages[u]
        self.send(f"{tag} OK expunged")


class SMTPHandler(socketserver.StreamRequestHandler):
    store: Store = None

    def send(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self.send("220 localbook test SMTP")
        sender, rcpts = "", []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\r\n")
            up = line.upper()
            if up.startswith("EHLO") or up.startswith("HELO"):
                self.send("250-localbook")
                self.send("250-AUTH PLAIN LOGIN")
                self.send("250 OK")
            elif up.startswith("AUTH PLAIN"):
                self.send("235 ok")
            elif up.startswith("AUTH LOGIN"):
                self.send("334 VXNlcm5hbWU6"); self.rfile.readline()
                self.send("334 UGFzc3dvcmQ6"); self.rfile.readline()
                self.send("235 ok")
            elif up.startswith("MAIL FROM"):
                sender = line.split(":", 1)[1].strip(); self.send("250 ok")
            elif up.startswith("RCPT TO"):
                rcpts.append(line.split(":", 1)[1].strip()); self.send("250 ok")
            elif up == "DATA":
                self.send("354 go")
                body = b""
                while True:
                    l = self.rfile.readline()
                    if l in (b".\r\n", b".\n") or not l:
                        break
                    body += l[1:] if l.startswith(b"..") else l
                name = time.strftime("%Y%m%d-%H%M%S") + ".eml"
                (self.store.outbox / name).write_bytes(body)
                log("SMTP: message from", sender, "to", rcpts, "->", self.store.outbox / name)
                self.send("250 queued")
            elif up == "QUIT":
                self.send("221 bye"); return
            elif up in ("RSET", "NOOP"):
                self.send("250 ok")
            else:
                self.send("250 ok")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default="testdata/flights/mail")
    ap.add_argument("--imap-port", type=int, default=1143)
    ap.add_argument("--smtp-port", type=int, default=1025)
    a = ap.parse_args()
    folder = Path(a.folder)
    if not folder.is_dir():
        sys.exit(f"no folder {folder}")
    store = Store(folder)
    IMAPHandler.store = store
    SMTPHandler.store = store
    imap = Server(("127.0.0.1", a.imap_port), IMAPHandler)
    smtp = Server(("127.0.0.1", a.smtp_port), SMTPHandler)
    threading.Thread(target=imap.serve_forever, daemon=True).start()
    threading.Thread(target=smtp.serve_forever, daemon=True).start()
    log(f"IMAP on 127.0.0.1:{a.imap_port} (INBOX: {len(store.messages)} messages), SMTP on 127.0.0.1:{a.smtp_port}; replies land in {store.outbox}")
    log("In LocalBook: IMAP account, host 127.0.0.1, port", a.imap_port, "TLS off, any user and password; SMTP host 127.0.0.1, port", a.smtp_port, "TLS off.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
