"""Pikafish Xiangqi GUI - PySide6 edition v25.

Windows front end for Pikafish running on a Raspberry Pi over SSH.
This is the first Qt/PySide6 port of the working Tkinter v19 GUI.
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import threading
import time
import tempfile
import wave
import struct
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPushButton, QSizePolicy, QSpinBox, QSplitter, QTextEdit, QVBoxLayout, QWidget, QInputDialog, QScrollArea
)

FILES, RANKS = 9, 10
START_FEN = "rheakaehr/9/1c5c1/s1s1s1s1s/9/9/S1S1S1S1S/1C5C1/9/RHEAKAEHR w - - 0 1"
ENGINE_START_FEN = START_FEN.translate(str.maketrans({
    "h": "n", "H": "N", "e": "b", "E": "B", "s": "p", "S": "P"
}))
NAMES = {
    "K": "帥", "A": "仕", "E": "相", "H": "傌", "R": "俥", "C": "炮", "S": "兵",
    "k": "將", "a": "士", "e": "象", "h": "馬", "r": "車", "c": "砲", "s": "卒",
}
EURO_NAMES = {
    "K": "♔", "A": "♕", "E": "🐘", "H": "♘", "R": "♖", "C": "◉", "S": "♙",
    "k": "♔", "a": "♕", "e": "🐘", "h": "♘", "r": "♖", "c": "◉", "s": "♙",
}

# Small, deliberately conservative built-in opening book used by the
# explorer/trainer.  Each key is the exact ICCS move prefix and each value is
# a list of (move, description) continuations.  It is intentionally modest:
# when coverage ends the GUI says so rather than pretending an unknown move is
# wrong.
OPENING_BOOK = {
    (): [("h2e2", "Central Cannon"), ("b2e2", "Central Cannon"),
         ("c0e2", "Elephant Opening"), ("g0e2", "Elephant Opening"),
         ("b0c2", "Horse Opening"), ("h0g2", "Horse Opening"),
         ("e3e4", "Central Pawn Opening")],
    ("h2e2",): [("b9c7", "Screen Horse Defence"), ("h9g7", "Screen Horse Defence")],
    ("b2e2",): [("b9c7", "Screen Horse Defence"), ("h9g7", "Screen Horse Defence")],
    ("h2e2","b9c7"): [("h0g2", "Develop the right horse"), ("b0c2", "Develop the left horse")],
    ("h2e2","h9g7"): [("h0g2", "Develop the right horse"), ("b0c2", "Develop the left horse")],
    ("b2e2","b9c7"): [("b0c2", "Develop the left horse"), ("h0g2", "Develop the right horse")],
    ("b2e2","h9g7"): [("b0c2", "Develop the left horse"), ("h0g2", "Develop the right horse")],
    ("h2e2","b9c7","h0g2"): [("h9g7", "Complete the Screen Horses")],
    ("h2e2","h9g7","h0g2"): [("b9c7", "Complete the Screen Horses")],
    ("b2e2","b9c7","b0c2"): [("h9g7", "Complete the Screen Horses")],
    ("b2e2","h9g7","b0c2"): [("b9c7", "Complete the Screen Horses")],
    ("c0e2",): [("c9e7", "Symmetrical elephant"), ("g9e7", "Symmetrical elephant"),
                 ("b9c7", "Develop a horse"), ("h9g7", "Develop a horse")],
    ("g0e2",): [("c9e7", "Symmetrical elephant"), ("g9e7", "Symmetrical elephant"),
                 ("b9c7", "Develop a horse"), ("h9g7", "Develop a horse")],
    ("b0c2",): [("b9c7", "Natural horse development"), ("h9g7", "Natural horse development")],
    ("h0g2",): [("b9c7", "Natural horse development"), ("h9g7", "Natural horse development")],
    ("e3e4",): [("e6e5", "Symmetrical central pawn"), ("b9c7", "Develop a horse"), ("h9g7", "Develop a horse")],
}

def opening_book_choices(moves, standard_start=True):
    if not standard_start:
        return []
    return list(OPENING_BOOK.get(tuple(m.lower() for m in moves), []))

def opening_book_departure(moves, standard_start=True):
    """Return first definite departure as (move_number, move, choices), else None.

    A departure is only reported when our built-in book explicitly has choices
    for that prefix.  If the book simply runs out of coverage, that is *not*
    labelled a mistake or departure.
    """
    if not standard_start:
        return None
    prefix=[]
    for idx, move in enumerate(moves):
        choices=opening_book_choices(prefix, True)
        if not choices:
            return None
        allowed={m for m,_ in choices}
        if move.lower() not in allowed:
            return idx+1, move.lower(), choices
        prefix.append(move.lower())
    return None


def recognize_opening(moves, standard_start=True):
    """Recognise a useful starter set of common Xiangqi opening families.

    This deliberately uses conservative ICCS move-pattern matching rather than
    pretending to be a complete opening encyclopaedia.  It names an opening
    family when the first move is characteristic, and adds a variation/setup
    only when the following moves give enough evidence.
    """
    if not standard_start:
        return "Custom position", "Opening recognition unavailable from a non-standard FEN"
    if not moves:
        return "Starting position", "—"

    seq=[m.lower() for m in moves]
    first=seq[0]

    # Red cannon from either wing to the centre file: the classic Central Cannon.
    if first in ("h2e2", "b2e2"):
        opening="Central Cannon"
        # Screen Horse / Screen Horses: Black develops one or both home horses
        # toward c7/g7.  We call one-horse development a setup rather than a
        # fully identified variation.
        black_moves=seq[1::2]
        screen_horses={"b9c7", "h9g7"}
        developed=[m for m in black_moves if m in screen_horses]
        if len(set(developed)) >= 2:
            variation="Screen Horse Defence"
        elif developed:
            variation="Screen Horse setup (developing)"
        elif black_moves and black_moves[0] in ("h7e7", "b7e7"):
            variation="Central Cannon reply"
        else:
            variation="—"
        return opening, variation

    # Elephant to the central e2 point.
    if first in ("c0e2", "g0e2"):
        return "Elephant Opening", "—"

    # Early horse development from either home horse.
    if first in ("b0c2", "h0g2"):
        return "Horse Opening", "—"

    # A first-move pawn advance.  Name the central pawn separately because it
    # is a common family; otherwise use the broader Pawn Opening label.
    if first == "e3e4":
        return "Central Pawn Opening", "—"
    if first in ("a3a4", "c3c4", "g3g4", "i3i4"):
        return "Pawn Opening", "—"

    # Less-common first moves remain deliberately unlabelled instead of being
    # assigned a possibly-wrong canonical opening name.
    return "Unrecognised / uncommon opening", "—"


def parse_fen(fen: str):
    parts = fen.split(); rows = parts[0].split("/"); board = {}
    for y, row in enumerate(rows):
        x = 0
        for ch in row:
            if ch.isdigit(): x += int(ch)
            else: board[(x, y)] = ch; x += 1
    return board, (parts[1] if len(parts) > 1 else "w")


def square_name(sq):
    x, y = sq
    return f"{chr(97+x)}{9-y}"


def parse_move(text):
    m = re.search(r"\b([a-i][0-9])([a-i][0-9])\b", text)
    if not m: return None
    def sq(s): return ord(s[0]) - 97, 9 - int(s[1])
    return sq(m.group(1)), sq(m.group(2))


def make_pgn(moves, result="*"):
    headers = [
        '[Event "Pikafish Xiangqi GUI Game"]', '[Site "?"]',
        f'[Date "{datetime.now().strftime("%Y.%m.%d")}"]', '[Round "?"]',
        '[Red "?"]', '[Black "?"]', f'[Result "{result}"]', '[Variant "Xiangqi"]',
        '[Format "ICCS"]', '[SetUp "1"]', f'[FEN "{ENGINE_START_FEN}"]',
    ]
    turns = []
    for i in range(0, len(moves), 2):
        turn = f"{i//2+1}. {moves[i]}"
        if i + 1 < len(moves): turn += " " + moves[i+1]
        turns.append(turn)
    movetext = " ".join(turns)
    return "\n".join(headers) + "\n\n" + (movetext + " " if movetext else "") + result + "\n"


def _review_eval_text(cp):
    """Format a review score stored in centipawns from the mover's viewpoint."""
    if cp is None:
        return "?"
    if cp >= 90000:
        return "+Mate"
    if cp <= -90000:
        return "-Mate"
    return f"{cp/100:+.2f}"


def make_annotated_pgn(moves, review_results, result="*"):
    """Write ICCS PGN with Pikafish review comments after matching moves."""
    headers = [
        '[Event "Pikafish Xiangqi GUI Game"]', '[Site "?"]',
        f'[Date "{datetime.now().strftime("%Y.%m.%d")}"]', '[Round "?"]',
        '[Red "?"]', '[Black "?"]', f'[Result "{result}"]', '[Variant "Xiangqi"]',
        '[Format "ICCS"]', '[SetUp "1"]', f'[FEN "{ENGINE_START_FEN}"]',
        '[Annotator "Pikafish"]',
    ]

    tokens=[]
    for i, move in enumerate(moves):
        if i % 2 == 0:
            tokens.append(f"{i//2+1}.")
        tokens.append(move)

        if i < len(review_results):
            r=review_results[i]
            # Only attach a review to the same move it was calculated for.
            if str(r.get("move", "")).lower() == str(move).lower():
                label=str(r.get("label", "Reviewed")).replace("★ ", "")
                before=r.get("before")
                after=r.get("after")
                mover_after=(-after if after is not None else None)
                bits=[label, f"eval before {_review_eval_text(before)}",
                      f"eval after {_review_eval_text(mover_after)}"]
                if r.get("loss") is not None:
                    bits.append(f"loss {r['loss']/100:.2f}")
                if r.get("best"):
                    bits.append(f"best {r['best']}")
                if r.get("pv"):
                    bits.append("PV " + str(r["pv"]))
                comment="; ".join(bits).replace("}", "]")
                tokens.append("{" + comment + "}")

    movetext=" ".join(tokens)
    return "\n".join(headers) + "\n\n" + (movetext + " " if movetext else "") + result + "\n"



def engine_fen_to_gui(fen: str):
    """Convert Pikafish/UCCI piece letters (n/b/p) to this GUI's h/e/s letters."""
    return fen.translate(str.maketrans({
        "n": "h", "N": "H", "b": "e", "B": "E", "p": "s", "P": "S"
    }))


def gui_fen_to_engine(fen: str):
    """Convert this GUI's h/e/s piece letters to Pikafish/UCCI n/b/p letters."""
    return fen.translate(str.maketrans({
        "h": "n", "H": "N", "e": "b", "E": "B", "s": "p", "S": "P"
    }))


def parse_pgn_text(text: str):
    """Read an ICCS/UCCI Xiangqi PGN and return (engine_fen, moves, result).

    The loader deliberately focuses on coordinate moves such as h2e2, which is
    the format written by this GUI and understood directly by Pikafish.  PGN
    headers, comments, move numbers, NAGs and simple variations are ignored.
    """
    headers = {}
    for key, value in re.findall(r'^\s*\[([^\s]+)\s+"(.*)"\]\s*$', text, flags=re.M):
        headers[key.lower()] = value

    body = re.sub(r'^\s*\[[^\n]*\]\s*$', ' ', text, flags=re.M)
    body = re.sub(r'\{.*?\}', ' ', body, flags=re.S)
    body = re.sub(r';[^\n]*', ' ', body)
    # Remove parenthesised side variations, including a few nested levels.
    previous = None
    while previous != body:
        previous = body
        body = re.sub(r'\([^()]*\)', ' ', body)

    moves = re.findall(r'(?i)(?<![a-z0-9])([a-i][0-9][a-i][0-9])(?![a-z0-9])', body)
    moves = [m.lower() for m in moves]
    result_match = re.search(r'(?<!\S)(1-0|0-1|1/2-1/2|\*)(?!\S)', body)
    result = headers.get('result', result_match.group(1) if result_match else '*')
    engine_fen = headers.get('fen', ENGINE_START_FEN)
    return engine_fen, moves, result


def same_side(a, b): return a.isupper() == b.isupper()


def path_count(board, a, b):
    ax, ay = a; bx, by = b
    if ax != bx and ay != by: return -1
    dx = (bx > ax) - (bx < ax); dy = (by > ay) - (by < ay)
    x, y, n = ax + dx, ay + dy, 0
    while (x, y) != (bx, by):
        n += (x, y) in board; x += dx; y += dy
    return n


def pseudo_legal(board, a, b):
    if a not in board or a == b: return False
    p = board[a]; target = board.get(b)
    if target and same_side(p, target): return False
    ax, ay = a; bx, by = b; dx, dy = bx-ax, by-ay; t = p.lower(); red = p.isupper()
    if t == "r": return path_count(board, a, b) == 0
    if t == "c": return path_count(board, a, b) == (1 if target else 0)
    if t == "h":
        if sorted((abs(dx), abs(dy))) != [1, 2]: return False
        leg = (ax + (dx//2 if abs(dx)==2 else 0), ay + (dy//2 if abs(dy)==2 else 0))
        return leg not in board
    if t == "e":
        return abs(dx)==2 and abs(dy)==2 and (ax+dx//2, ay+dy//2) not in board and ((by >= 5) if red else (by <= 4))
    if t == "a":
        return abs(dx)==1 and abs(dy)==1 and 3 <= bx <= 5 and ((7 <= by <= 9) if red else (0 <= by <= 2))
    if t == "k":
        if target and target.lower()=="k" and ax==bx: return path_count(board,a,b)==0
        return abs(dx)+abs(dy)==1 and 3 <= bx <= 5 and ((7 <= by <= 9) if red else (0 <= by <= 2))
    if t == "s":
        forward = -1 if red else 1; crossed = ay <= 4 if red else ay >= 5
        return (dx==0 and dy==forward) or (crossed and dy==0 and abs(dx)==1)
    return False


def kings_face(board):
    red = next((s for s,p in board.items() if p=="K"), None)
    black = next((s for s,p in board.items() if p=="k"), None)
    return bool(red and black and red[0]==black[0] and path_count(board, red, black)==0)


def in_check(board, red):
    king = next((sq for sq,p in board.items() if p == ("K" if red else "k")), None)
    if king is None: return True
    return any(p.isupper() != red and pseudo_legal(board, sq, king) for sq,p in board.items())


def legal_destinations(board, source):
    piece = board.get(source)
    if not piece: return []
    red = piece.isupper(); result = []
    for y in range(RANKS):
        for x in range(FILES):
            target = (x,y)
            if not pseudo_legal(board, source, target): continue
            test = dict(board); test[target] = test.pop(source)
            if not kings_face(test) and not in_check(test, red): result.append(target)
    return result


def is_checkmate(board, red):
    if not in_check(board, red): return False
    return not any(legal_destinations(board, sq) for sq,p in board.items() if p.isupper() == red)


@dataclass
class EngineSettings:
    host: str = ""
    port: int = 22
    username: str = ""
    engine: str = ""
    keyfile: str = ""
    depth: int = 16
    threads: int = 4
    hash_mb: int = 256
    analysis_seconds: int = 10


class RemoteEngine:
    def __init__(self, out_queue):
        self.q = out_queue; self.proc = None

    def connect(self, cfg, password=""):
        self.close()
        cmd = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", str(cfg.port)]
        if cfg.keyfile: cmd += ["-i", os.path.expanduser(cfg.keyfile)]
        cmd += [f"{cfg.username}@{cfg.host}", cfg.engine]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1,
                                     creationflags=flags)
        time.sleep(.4)
        if self.proc.poll() is not None:
            detail = self.proc.stdout.read().strip() if self.proc.stdout else ""
            raise RuntimeError(detail or "Windows SSH could not start Pikafish")
        threading.Thread(target=self._reader, daemon=True).start()
        self.send("uci"); time.sleep(.15)
        self.send(f"setoption name Threads value {cfg.threads}")
        self.send(f"setoption name Hash value {cfg.hash_mb}")
        self.send("isready")

    def _reader(self):
        while self.proc and self.proc.stdout:
            line = self.proc.stdout.readline()
            if not line: break
            self.q.put(line.rstrip("\r\n"))

    def send(self, cmd):
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            raise RuntimeError("Not connected to Pikafish")
        self.proc.stdin.write(cmd + "\n"); self.proc.stdin.flush()

    def close(self):
        try:
            if self.proc and self.proc.poll() is None:
                if self.proc.stdin:
                    self.proc.stdin.write("quit\n"); self.proc.stdin.flush()
                self.proc.terminate()
        except Exception:
            pass
        self.proc = None


class BoardWidget(QWidget):
    squareClicked = Signal(tuple)

    def __init__(self, app):
        super().__init__(); self.app = app
        self.setMinimumSize(540, 560); self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def geometry_values(self):
        margin_x, margin_y = 76.0, 72.0
        usable_w = max(400.0, self.width() - 2*margin_x)
        usable_h = max(500.0, self.height() - 2*margin_y)
        sx = min(usable_w/8.0, usable_h/9.0)
        sy = sx
        board_w, board_h = 8*sx, 9*sy
        ox = (self.width()-board_w)/2.0
        oy = (self.height()-board_h)/2.0
        return ox, oy, sx, sy

    def screen_sq(self, x, y): return (8-x, 9-y) if self.app.flipped else (x,y)
    def board_sq(self, x, y): return (8-x, 9-y) if self.app.flipped else (x,y)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton: return
        ox,oy,sx,sy = self.geometry_values()
        x = round((event.position().x()-ox)/sx); y = round((event.position().y()-oy)/sy)
        if 0 <= x < 9 and 0 <= y < 10: self.squareClicked.emit(self.board_sq(x,y))

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#f7e6c9"))
        ox,oy,sx,sy = self.geometry_values(); grid = QColor("#b56a22")

        # frame / board
        board_rect = QRectF(ox-62, oy-44, 8*sx+124, 9*sy+104)
        p.setPen(QPen(QColor("#c9a36b"), 7)); p.setBrush(QColor("#f4dfbd")); p.drawRoundedRect(board_rect, 13, 13)
        p.setPen(QPen(QColor("#efd4aa"), 1))
        yy = board_rect.top()+18
        while yy < board_rect.bottom()-12:
            p.drawLine(QPointF(board_rect.left()+8, yy), QPointF(board_rect.right()-8, yy)); yy += 24

        p.setPen(QPen(grid, 2))
        for y in range(10): p.drawLine(QPointF(ox,oy+y*sy), QPointF(ox+8*sx,oy+y*sy))
        for x in range(9):
            xx = ox+x*sx
            if x in (0,8): p.drawLine(QPointF(xx,oy), QPointF(xx,oy+9*sy))
            else:
                p.drawLine(QPointF(xx,oy), QPointF(xx,oy+4*sy)); p.drawLine(QPointF(xx,oy+5*sy), QPointF(xx,oy+9*sy))
        for y0 in (0,7):
            p.drawLine(QPointF(ox+3*sx,oy+y0*sy), QPointF(ox+5*sx,oy+(y0+2)*sy))
            p.drawLine(QPointF(ox+5*sx,oy+y0*sy), QPointF(ox+3*sx,oy+(y0+2)*sy))

        # Clear, roomy Pikafish/ICCS coordinates.
        # Files a-i are along the bottom; ranks 0-9 run vertically on both sides.
        coord_size = max(15, int(sx * 0.28))
        p.setFont(QFont("Segoe UI", coord_size, QFont.Bold))
        p.setPen(QColor("#9b4f12"))

        for screen_x in range(FILES):
            file_index = (8-screen_x) if self.app.flipped else screen_x
            px = ox + screen_x*sx
            p.drawText(QRectF(px-24, oy+9*sy+20, 48, 32),
                       Qt.AlignCenter, chr(ord("a")+file_index))

        for screen_y in range(RANKS):
            rank = screen_y if self.app.flipped else (9-screen_y)
            py = oy + screen_y*sy
            # Wider boxes and more clearance from the outside files so 0-9 never look cramped.
            p.drawText(QRectF(ox-62, py-16, 40, 32), Qt.AlignCenter, str(rank))
            p.drawText(QRectF(ox+8*sx+22, py-16, 40, 32), Qt.AlignCenter, str(rank))

        river_y = oy+4.5*sy
        p.setPen(grid); p.setFont(QFont("Microsoft YaHei", max(14,int(sx*.30)), QFont.Bold))
        p.drawText(QRectF(ox+.4*sx,river_y-22,2.6*sx,44), Qt.AlignCenter, "楚 河")
        p.drawText(QRectF(ox+5.0*sx,river_y-22,2.6*sx,44), Qt.AlignCenter, "漢 界")
        p.setFont(QFont("Georgia", max(9,int(sx*.16)), QFont.Bold))
        p.drawText(QRectF(ox+3.1*sx,river_y-18,1.8*sx,36), Qt.AlignCenter, "Xiangqi GUI")

        # last move
        if self.app.moves:
            mv = parse_move(self.app.moves[-1])
            if mv:
                a,b = mv; ax,ay = self.screen_sq(*a); bx,by = self.screen_sq(*b)
                a_pt = QPointF(ox+ax*sx,oy+ay*sy); b_pt = QPointF(ox+bx*sx,oy+by*sy)
                p.setPen(QPen(QColor("#d17a00"), 4)); p.drawLine(a_pt,b_pt)
                p.setBrush(Qt.NoBrush); p.drawEllipse(a_pt,16,16); p.drawEllipse(b_pt,30,30)

        # engine hint: highlight the suggested from/to squares without making the move
        if self.app.hint_move:
            ha,hb = self.app.hint_move
            hax,hay = self.screen_sq(*ha); hbx,hby = self.screen_sq(*hb)
            a_pt = QPointF(ox+hax*sx, oy+hay*sy); b_pt = QPointF(ox+hbx*sx, oy+hby*sy)
            hint_pen = QPen(QColor("#7a3cff"), 5)
            hint_pen.setCapStyle(Qt.RoundCap)
            p.setPen(hint_pen); p.setBrush(Qt.NoBrush)
            p.drawEllipse(a_pt, 29, 29); p.drawEllipse(b_pt, 29, 29); p.drawLine(a_pt, b_pt)

        # Best-line arrows from Pikafish principal variation (next 3-5 moves).
        if self.app.best_line_moves:
            arrow_cols = [QColor("#2166ac"), QColor("#4d7fb8"), QColor("#7698c5"), QColor("#9ab0ce"), QColor("#b5c4d5")]
            for idx, (aa, bb) in enumerate(self.app.best_line_moves[:5]):
                ax, ay = self.screen_sq(*aa); bx, by = self.screen_sq(*bb)
                start = QPointF(ox+ax*sx, oy+ay*sy); end = QPointF(ox+bx*sx, oy+by*sy)
                col = arrow_cols[min(idx, len(arrow_cols)-1)]
                pen = QPen(col, max(2.2, 5.0-idx*0.6)); pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen); p.setBrush(col); p.drawLine(start, end)
                dx, dy = end.x()-start.x(), end.y()-start.y(); length = math.hypot(dx,dy)
                if length > 1:
                    ux, uy = dx/length, dy/length; px, py = -uy, ux
                    tip = QPointF(end.x()-ux*10, end.y()-uy*10)
                    wing1 = QPointF(tip.x()-ux*10+px*6, tip.y()-uy*10+py*6)
                    wing2 = QPointF(tip.x()-ux*10-px*6, tip.y()-uy*10-py*6)
                    p.drawPolygon(QPolygonF([tip, wing1, wing2]))

        # legal targets / selection
        if self.app.selected:
            for target in legal_destinations(self.app.board, self.app.selected):
                x,y = self.screen_sq(*target); pt = QPointF(ox+x*sx, oy+y*sy)
                if target in self.app.board:
                    p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor("#16803c"), 5)); p.drawEllipse(pt,31,31)
                else:
                    p.setBrush(QColor("#16803c")); p.setPen(Qt.NoPen); p.drawEllipse(pt,8,8)
            x,y = self.screen_sq(*self.app.selected); pt = QPointF(ox+x*sx,oy+y*sy)
            p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor("#1d70b8"),4)); p.drawEllipse(pt,30,30)

        for sq,piece in self.app.board.items():
            x,y = self.screen_sq(*sq); cx,cy = ox+x*sx,oy+y*sy
            radius = min(25.0, sx*.36)
            fill = QColor("#c9342c") if piece.isupper() else QColor("#2f3032")
            p.setPen(Qt.NoPen); p.setBrush(QColor("#8a6c50")); p.drawEllipse(QPointF(cx+3,cy+4),radius,radius)
            p.setBrush(fill); p.drawEllipse(QPointF(cx,cy),radius,radius)
            if self.app.piece_style == "chinese":
                p.setBrush(Qt.NoBrush); p.setPen(QPen(Qt.white,2)); p.drawEllipse(QPointF(cx,cy),radius-4,radius-4)
            if self.app.piece_style == "european" and piece.lower() == "c":
                self.draw_cannon(p,cx,cy,piece.isupper(),radius)
            else:
                labels = NAMES if self.app.piece_style == "chinese" else EURO_NAMES
                if self.app.piece_style == "chinese":
                    font_name = "Microsoft YaHei"
                elif piece.lower() == "e":
                    font_name = "Segoe UI Emoji"
                else:
                    font_name = "Segoe UI Symbol"
                p.setFont(QFont(font_name, max(14,int(radius*.82)), QFont.Bold if self.app.piece_style=="chinese" else QFont.Normal))
                p.setPen(Qt.white); p.drawText(QRectF(cx-radius,cy-radius,2*radius,2*radius),Qt.AlignCenter,labels[piece])

        checked_red = self.app.turn == "w"; checked = in_check(self.app.board, checked_red)
        mate = is_checkmate(self.app.board,checked_red) if checked else False
        if checked:
            general = next((sq for sq,p in self.app.board.items() if p == ("K" if checked_red else "k")),None)
            if general and (mate or self.app.check_flash_on):
                gx,gy = self.screen_sq(*general); pt = QPointF(ox+gx*sx,oy+gy*sy)
                p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor("#e00000"),6)); p.drawEllipse(pt,32,32)
                if mate: p.drawLine(QPointF(pt.x()-23,pt.y()-23),QPointF(pt.x()+23,pt.y()+23))

    def draw_cannon(self,p,cx,cy,red,radius):
        # Same orientation for all four cannons: wheels always down on screen.
        scale = radius / 25.0 * .84
        def pt(x,y): return QPointF(cx+x*scale,cy+y*scale)
        p.setPen(Qt.NoPen); p.setBrush(Qt.white)
        poly = QPolygonF([pt(-12,0),pt(10,-13),pt(15,-8),pt(-7,5)]); p.drawPolygon(poly)
        p.setPen(QPen(Qt.white,max(2.2,4*scale),Qt.SolidLine,Qt.RoundCap)); p.drawLine(pt(10,-15),pt(16,-9))
        path = QPainterPath(pt(-10,0)); path.cubicTo(pt(-16,5),pt(-16,12),pt(-8,13)); path.cubicTo(pt(-4,13),pt(-1,11),pt(1,9))
        p.setPen(QPen(Qt.white,max(1.8,3*scale),Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin)); p.setBrush(Qt.NoBrush); p.drawPath(path)
        p.setBrush(Qt.white); p.setPen(Qt.NoPen); p.drawEllipse(pt(-15,7),2.5*scale,2.5*scale)
        wx,wy = pt(7.8,10).x(),pt(7.8,10).y(); r = 7.0*scale
        p.setBrush(Qt.NoBrush); p.setPen(QPen(Qt.white,max(1.5,2*scale))); p.drawEllipse(QPointF(wx,wy),r,r)
        p.setBrush(QColor("#c9342c") if red else QColor("#2f3032")); p.drawEllipse(QPointF(wx,wy),2.0*scale,2.0*scale)
        p.setPen(QPen(Qt.white,max(1.0,1.2*scale)))
        for angle in range(0,360,45):
            rad=math.radians(angle)
            p.drawLine(QPointF(wx+2.6*scale*math.cos(rad),wy+2.6*scale*math.sin(rad)),
                       QPointF(wx+5.8*scale*math.cos(rad),wy+5.8*scale*math.sin(rad)))


class ConnectionDialog(QDialog):
    def __init__(self,cfg,parent=None):
        super().__init__(parent); self.setWindowTitle("Raspberry Pi connection"); self.cfg=cfg; form=QFormLayout(self)
        self.host=QLineEdit(cfg.host); self.port=QSpinBox(); self.port.setRange(1,65535); self.port.setValue(cfg.port)
        self.username=QLineEdit(cfg.username); self.engine=QLineEdit(cfg.engine); self.keyfile=QLineEdit(cfg.keyfile)
        self.depth=QSpinBox(); self.depth.setRange(1,100); self.depth.setValue(cfg.depth)
        self.threads=QSpinBox(); self.threads.setRange(1,128); self.threads.setValue(cfg.threads)
        self.hash_mb=QSpinBox(); self.hash_mb.setRange(16,32768); self.hash_mb.setValue(cfg.hash_mb)
        self.analysis_seconds=QSpinBox(); self.analysis_seconds.setRange(1,3600); self.analysis_seconds.setValue(cfg.analysis_seconds)
        self.password=QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        for label,w in [("Host",self.host),("Port",self.port),("Username",self.username),("Engine path",self.engine),("SSH key (optional)",self.keyfile),("Depth",self.depth),("Threads",self.threads),("Hash MB",self.hash_mb),("Analyse seconds",self.analysis_seconds),("Password (not saved)",self.password)]: form.addRow(label,w)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)
    def values(self):
        return EngineSettings(self.host.text().strip(),self.port.value(),self.username.text().strip(),self.engine.text().strip(),self.keyfile.text().strip(),self.depth.value(),self.threads.value(),self.hash_mb.value(),self.analysis_seconds.value())


class EvaluationGraph(QWidget):
    """Small clickable Red-vs-Black evaluation graph built from game review scores."""
    positionSelected = Signal(int)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setMinimumHeight(105)
        self.setMaximumHeight(125)
        self.setToolTip("Evaluation graph — click a point to jump to that position")

    def _points(self):
        return self.app.evaluation_graph_points()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pts = self._points()
        if not pts:
            return
        left, right = 42.0, 12.0
        width = max(1.0, self.width() - left - right)
        max_index = max(1, max(i for i, _ in pts))
        target = round((event.position().x() - left) / width * max_index)
        nearest = min(pts, key=lambda it: abs(it[0] - target))
        self.positionSelected.emit(nearest[0])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#fbf7ef"))
        p.setPen(QPen(QColor("#d6c8b5"), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

        left, right, top, bottom = 42.0, 12.0, 16.0, 28.0
        w = max(1.0, self.width() - left - right)
        h = max(1.0, self.height() - top - bottom)
        mid = top + h / 2.0

        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor("#666"))
        p.drawText(QRectF(4, top-7, 34, 18), Qt.AlignRight | Qt.AlignVCenter, "Red")
        p.drawText(QRectF(4, top+h-9, 34, 18), Qt.AlignRight | Qt.AlignVCenter, "Black")
        p.setPen(QPen(QColor("#aaa"), 1, Qt.DashLine))
        p.drawLine(QPointF(left, mid), QPointF(left+w, mid))

        pts = self._points()
        if not pts:
            p.setPen(QColor("#777"))
            p.drawText(QRectF(left, top, w, h), Qt.AlignCenter, "Run Review Game to build the evaluation graph")
            return

        max_index = max(1, max(i for i, _ in pts))
        # Compress extreme engine values so one mate score does not flatten the whole graph.
        clipped = [(i, max(-800, min(800, score))) for i, score in pts]
        scale = h / 2.0 / 800.0
        qpts = [QPointF(left + (i/max_index)*w, mid - score*scale) for i, score in clipped]

        p.setPen(QPen(QColor("#555"), 2))
        for a, b in zip(qpts, qpts[1:]):
            p.drawLine(a, b)
        p.setBrush(QColor("#555")); p.setPen(Qt.NoPen)
        for pt in qpts:
            p.drawEllipse(pt, 3.2, 3.2)

        # Mark the currently displayed loaded-PGN position.
        if self.app.loaded_game_moves is not None:
            idx = self.app.loaded_index
            x = left + (idx/max_index)*w
            p.setPen(QPen(QColor("#7a3cff"), 2))
            p.drawLine(QPointF(x, top), QPointF(x, top+h))

        p.setPen(QColor("#666"))
        p.drawText(QRectF(left, top+h+4, w, 20), Qt.AlignCenter, "Position / move number")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Pikafish Xiangqi - Public v1.0.1"); self.resize(1080,700); self.setMinimumSize(900,620)
        self.board,self.turn=parse_fen(START_FEN); self.piece_style="european"; self.check_flash_on=True
        self.moves=[]; self.history=[]; self.selected=None; self.flipped=False; self.pending=None; self.analysis_running=False; self.hint_move=None; self.last_analysis_depth=None
        self.last_engine_score=None; self.last_engine_pv=""; self.best_line_moves=[]
        # MultiPV analysis: rank -> {score_text, score_cp, depth, pv}.
        # Normal engine actions still use MultiPV=1; Analyse temporarily requests 3 lines.
        self.multipv_lines={}
        self.review_running=False; self.review_moves=[]; self.review_results=[]; self.review_index=0; self.coach_record=None
        self.review_phase=None; self.review_before_score=None; self.review_bestmove=None; self.review_before_pv=""; self.review_seconds=2
        # Retry-mistakes trainer state.
        self.retry_mode=False; self.retry_indices=[]; self.retry_cursor=0; self.retry_expected=None; self.coach_hint_level=0
        # Small persistent local stores (JSON in the user's home directory).
        self.positions_path=os.path.join(os.path.expanduser("~"), ".pikafish_positions.json")
        self.stats_path=os.path.join(os.path.expanduser("~"), ".pikafish_game_stats.json")
        self.engine_base_fen=ENGINE_START_FEN; self.gui_base_fen=START_FEN
        self.loaded_game_moves=None; self.loaded_game_result="*"; self.loaded_index=0; self.loaded_filename=""
        # Play-vs-Pikafish mode.  Difficulty is intentionally implemented with
        # search limits rather than an Elo claim: Easy/Medium/Hard are relative
        # levels for this GUI, not calibrated ratings.
        self.play_mode=False; self.human_side="w"; self.play_difficulty="Medium"
        # Opening explorer / trainer state.  The trainer checks only the small
        # built-in book above; it never calls an unfamiliar move "wrong" once
        # the book has run out of coverage.
        self.training_mode=False; self.training_message="Training: off"
        self.engine_q=queue.Queue(); self.engine=RemoteEngine(self.engine_q)
        self.sound_q=queue.Queue(); self.sound_path=self._prepare_move_sound(); threading.Thread(target=self._sound_worker, daemon=True).start()
        self.cfg_path=os.path.join(os.path.expanduser("~"),".pikafish_xiangqi_public.json"); self.cfg=self.load_cfg()
        self.build_ui(); self.update_state_labels()
        self.poll_timer=QTimer(self); self.poll_timer.timeout.connect(self.poll_engine); self.poll_timer.start(80)
        self.flash_timer=QTimer(self); self.flash_timer.timeout.connect(self.flash_check); self.flash_timer.start(500)

    def load_cfg(self):
        try:
            with open(self.cfg_path,encoding="utf8") as f:
                data=json.load(f); allowed=EngineSettings.__dataclass_fields__.keys(); return EngineSettings(**{k:v for k,v in data.items() if k in allowed})
        except Exception: return EngineSettings()
    def save_cfg(self):
        with open(self.cfg_path,"w",encoding="utf8") as f: json.dump(vars(self.cfg),f,indent=2)

    def build_ui(self):
        root=QWidget(); self.setCentralWidget(root); outer=QVBoxLayout(root); outer.setContentsMargins(6,6,6,6); outer.setSpacing(5)

        # Keep only the everyday board/PGN controls across the top so the
        # toolbar does not force the whole application to become very wide.
        bar=QHBoxLayout()
        for text,fn in [("New game",self.new_game),("Undo",self.undo),("Flip board",self.flip),
                        ("Load PGN",self.load_pgn),("Save PGN",self.save_pgn),
                        ("◀ Back",self.pgn_back),("Forward ▶",self.pgn_forward)]:
            b=QPushButton(text); b.clicked.connect(fn); bar.addWidget(b)
        self.style_button=QPushButton("Pieces: Symbols")
        self.style_button.clicked.connect(self.toggle_piece_style); bar.addWidget(self.style_button)
        bar.addStretch(1); self.status=QLabel("Not connected"); bar.addWidget(self.status)
        outer.addLayout(bar)

        splitter=QSplitter(Qt.Horizontal); outer.addWidget(splitter,1)
        self.board_widget=BoardWidget(self); self.board_widget.squareClicked.connect(self.click_square); splitter.addWidget(self.board_widget)
        right_scroll=QScrollArea(); right_scroll.setWidgetResizable(True); right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right=QFrame(); right.setMinimumWidth(330); rl=QVBoxLayout(right); rl.setContentsMargins(8,4,8,6); rl.setSpacing(5)

        title=QLabel("Pikafish controls"); title.setStyleSheet("font-size:16px;font-weight:700"); rl.addWidget(title)

        # Connection / immediate engine actions.
        engine_row=QHBoxLayout()
        for text,fn in [("Connect",self.connect_dialog),("Hint",self.hint_move_request),("Engine move",self.engine_move)]:
            b=QPushButton(text); b.clicked.connect(fn); engine_row.addWidget(b)
        rl.addLayout(engine_row)

        # Position/FEN controls. Keep these in the right panel so the top toolbar stays compact.
        fen_row=QHBoxLayout(); fen_row.addWidget(QLabel("Position:"))
        self.copy_fen_button=QPushButton("Copy FEN")
        self.copy_fen_button.setToolTip("Copy the current board position as a Pikafish/Xiangqi FEN string")
        self.copy_fen_button.clicked.connect(self.copy_fen)
        self.load_fen_button=QPushButton("Load FEN")
        self.load_fen_button.setToolTip("Paste a Pikafish/Xiangqi FEN position and load it onto the board")
        self.load_fen_button.clicked.connect(self.load_fen_dialog)
        self.save_position_button=QPushButton("Save Position")
        self.save_position_button.setToolTip("Save the current FEN locally with a memorable name")
        self.save_position_button.clicked.connect(self.save_interesting_position)
        fen_row.addWidget(self.copy_fen_button); fen_row.addWidget(self.load_fen_button); fen_row.addWidget(self.save_position_button); fen_row.addStretch(1)
        rl.addLayout(fen_row)

        self.opening_label=QLabel("Opening: Starting position\nVariation: —")
        self.opening_label.setWordWrap(True)
        self.opening_label.setStyleSheet("font-weight:600; padding:4px 0")
        self.opening_label.setToolTip("Opening recognition uses a conservative built-in set of common Xiangqi opening patterns")
        rl.addWidget(self.opening_label)

        explorer_title=QLabel("Opening explorer")
        explorer_title.setStyleSheet("font-weight:700; margin-top:2px")
        rl.addWidget(explorer_title)
        self.book_moves_label=QLabel("Book moves: —")
        self.book_moves_label.setWordWrap(True)
        self.book_moves_label.setToolTip("Common continuations from the GUI's small built-in opening book")
        rl.addWidget(self.book_moves_label)
        book_buttons=QHBoxLayout()
        self.book_move_buttons=[]
        for _ in range(3):
            b=QPushButton("")
            b.hide()
            b.setToolTip("Highlight this book move on the board")
            b.clicked.connect(lambda checked=False, btn=b: self.show_book_move(btn.property("book_move")))
            self.book_move_buttons.append(b); book_buttons.addWidget(b)
        book_buttons.addStretch(1); rl.addLayout(book_buttons)
        train_row=QHBoxLayout()
        self.training_button=QPushButton("Start Opening Trainer")
        self.training_button.clicked.connect(self.toggle_opening_training)
        train_row.addWidget(self.training_button)
        self.training_status=QLabel("Training: off")
        self.training_status.setWordWrap(True); train_row.addWidget(self.training_status,1)
        rl.addLayout(train_row)

        # Position analysis controls.
        analyse_row=QHBoxLayout(); analyse_row.addWidget(QLabel("Analyse:"))
        self.analysis_time=QComboBox(); self.analysis_time.addItems(["5 s","10 s","30 s","60 s"])
        preferred=f"{self.cfg.analysis_seconds} s"
        if preferred not in [self.analysis_time.itemText(i) for i in range(self.analysis_time.count())]: self.analysis_time.addItem(preferred)
        self.analysis_time.setCurrentText(preferred); self.analysis_time.setToolTip("How long Pikafish should analyse the current position")
        analyse_row.addWidget(self.analysis_time)
        self.analyse_button=QPushButton("Analyse"); self.analyse_button.clicked.connect(self.toggle_analysis); analyse_row.addWidget(self.analyse_button)
        analyse_row.addStretch(1); rl.addLayout(analyse_row)

        # Whole-game review controls.
        review_row=QHBoxLayout(); review_row.addWidget(QLabel("Review:"))
        self.review_time=QComboBox(); self.review_time.addItems(["1 s/move","2 s/move","5 s/move"]); self.review_time.setCurrentText("2 s/move")
        self.review_time.setToolTip("Time Pikafish spends on each position during automatic game review")
        review_row.addWidget(self.review_time)
        self.review_button=QPushButton("Review Game"); self.review_button.clicked.connect(self.toggle_game_review)
        self.review_button.setToolTip("Analyse the whole game and label each move Best, Good, Inaccuracy, Mistake or Blunder")
        review_row.addWidget(self.review_button); review_row.addStretch(1); rl.addLayout(review_row)

        # Play against Pikafish controls.
        play_row=QHBoxLayout(); play_row.addWidget(QLabel("Play:"))
        self.play_level=QComboBox(); self.play_level.addItems(["Easy","Medium","Hard"]); self.play_level.setCurrentText("Medium")
        self.play_level.setToolTip("Relative Pikafish playing strength: Easy searches shallowly, Hard searches much deeper")
        self.play_level.currentTextChanged.connect(self._play_level_changed); play_row.addWidget(self.play_level)
        self.play_side=QComboBox(); self.play_side.addItems(["You: Red","You: Black"]); self.play_side.setCurrentText("You: Red")
        self.play_side.setToolTip("Choose which colour you want before starting a game against Pikafish"); play_row.addWidget(self.play_side)
        self.play_button=QPushButton("Play Pikafish"); self.play_button.clicked.connect(self.toggle_play_mode)
        self.play_button.setToolTip("Start or stop a game against Pikafish at the selected level"); play_row.addWidget(self.play_button)
        rl.addLayout(play_row)

        title2=QLabel("Pikafish analysis"); title2.setStyleSheet("font-size:15px;font-weight:700; margin-top:2px"); rl.addWidget(title2)
        self.eval_label=QLabel("Score: —    Depth: —"); rl.addWidget(self.eval_label)
        self.analysis=QTextEdit(); self.analysis.setReadOnly(True); self.analysis.setFont(QFont("Consolas",10))
        self.analysis.setMaximumHeight(100); self.analysis.setMinimumHeight(58); rl.addWidget(self.analysis,0)
        self.review_summary=QLabel("Game review: not run"); self.review_summary.setWordWrap(True); self.review_summary.setStyleSheet("font-weight:600"); rl.addWidget(self.review_summary)
        mistake_row=QHBoxLayout()
        self.prev_mistake_button=QPushButton("◀ Previous mistake")
        self.next_mistake_button=QPushButton("Next mistake ▶")
        self.prev_mistake_button.setToolTip("Jump to the previous move classified as a Mistake or Blunder")
        self.next_mistake_button.setToolTip("Jump to the next move classified as a Mistake or Blunder")
        self.prev_mistake_button.clicked.connect(lambda: self.navigate_mistake(-1))
        self.next_mistake_button.clicked.connect(lambda: self.navigate_mistake(1))
        self.prev_mistake_button.setEnabled(False); self.next_mistake_button.setEnabled(False)
        mistake_row.addWidget(self.prev_mistake_button); mistake_row.addWidget(self.next_mistake_button); mistake_row.addStretch(1)
        rl.addLayout(mistake_row)
        coach_title=QLabel("Post-game Coach"); coach_title.setStyleSheet("font-size:14px;font-weight:700; margin-top:4px"); rl.addWidget(coach_title)
        self.coach_title=QLabel("Run Review Game, then select a Mistake or Blunder."); self.coach_title.setWordWrap(True); self.coach_title.setStyleSheet("font-weight:700"); rl.addWidget(self.coach_title)
        self.coach_detail=QLabel(""); self.coach_detail.setWordWrap(True); rl.addWidget(self.coach_detail)
        self.coach_prompt=QLabel(""); self.coach_prompt.setWordWrap(True); rl.addWidget(self.coach_prompt)
        coach_row=QHBoxLayout()
        self.coach_hint_button=QPushButton("Show Hint"); self.coach_best_button=QPushButton("Show Best Move"); self.coach_continue_button=QPushButton("Continue Analysis")
        self.coach_hint_button.clicked.connect(self.coach_show_hint); self.coach_best_button.clicked.connect(self.coach_show_best); self.coach_continue_button.clicked.connect(self.coach_continue_analysis)
        for b in (self.coach_hint_button,self.coach_best_button,self.coach_continue_button): b.setEnabled(False); coach_row.addWidget(b)
        rl.addLayout(coach_row)
        trainer_row=QHBoxLayout()
        self.retry_button=QPushButton("Retry Mistakes")
        self.retry_button.setToolTip("Step through every reviewed Mistake/Blunder and try to find Pikafish's better move")
        self.retry_button.clicked.connect(self.toggle_retry_mistakes)
        self.retry_button.setEnabled(False)
        self.stats_button=QPushButton("Game Statistics")
        self.stats_button.setToolTip("Show statistics accumulated from completed game reviews")
        self.stats_button.clicked.connect(self.show_game_statistics)
        trainer_row.addWidget(self.retry_button); trainer_row.addWidget(self.stats_button); trainer_row.addStretch(1)
        rl.addLayout(trainer_row)
        self.eval_graph=EvaluationGraph(self); self.eval_graph.setMinimumHeight(105); self.eval_graph.setMaximumHeight(125); self.eval_graph.positionSelected.connect(self.jump_to_graph_position); rl.addWidget(self.eval_graph)
        rl.addWidget(QLabel("Moves (click a loaded move to jump there)")); self.movelist=QListWidget(); self.movelist.setMinimumHeight(190)
        self.movelist.setFont(QFont("Consolas",10)); self.movelist.itemClicked.connect(self.jump_to_pgn_item); rl.addWidget(self.movelist,1)
        cap=QLabel("Captured pieces"); cap.setStyleSheet("font-weight:700"); rl.addWidget(cap)
        self.captured_red=QLabel("Taken by Red: None"); self.captured_black=QLabel("Taken by Black: None"); rl.addWidget(self.captured_red); rl.addWidget(self.captured_black)
        self.turn_label=QLabel(); self.turn_label.setStyleSheet("font-weight:700"); rl.addWidget(self.turn_label)
        rl.addStretch(1)
        right_scroll.setWidget(right)
        splitter.addWidget(right_scroll); splitter.setSizes([690,360]); splitter.setStretchFactor(0,1); splitter.setStretchFactor(1,0)

    def update_state_labels(self):
        self.update_captured(); checked_red=self.turn=="w"; checked=in_check(self.board,checked_red); mate=is_checkmate(self.board,checked_red) if checked else False
        side="Red" if self.turn=="w" else "Black"; self.turn_label.setText(side+(" is checkmated" if mate else " is in check" if checked else " to move"))
        self.update_opening_label()
        self.board_widget.update()

    def update_opening_label(self):
        standard_start = self.engine_base_fen.split()[0] == ENGINE_START_FEN.split()[0]
        opening, variation = recognize_opening(self.moves, standard_start)
        self.opening_label.setText(f"Opening: {opening}\nVariation: {variation}")
        self.update_opening_explorer()

    def update_opening_explorer(self):
        if not hasattr(self, "book_moves_label"):
            return
        standard_start = self.engine_base_fen.split()[0] == ENGINE_START_FEN.split()[0]
        choices=opening_book_choices(self.moves, standard_start)
        if not standard_start:
            self.book_moves_label.setText("Book moves: unavailable for a custom FEN")
        elif choices:
            self.book_moves_label.setText("Book moves: " + "   •   ".join(f"{m} — {desc}" for m,desc in choices))
        else:
            self.book_moves_label.setText("Book moves: no further built-in book coverage")
        for idx,b in enumerate(self.book_move_buttons):
            if idx < len(choices):
                move,desc=choices[idx]; b.setText(move); b.setProperty("book_move", move); b.setToolTip(desc); b.show()
            else:
                b.setProperty("book_move", ""); b.hide()
        if not self.training_mode:
            self.training_status.setText("Training: off")

    def show_book_move(self, move_text):
        if not move_text:
            return
        mv=parse_move(move_text)
        if mv:
            self.hint_move=mv
            self.status.setText(f"Book move: {move_text}")
            self.board_widget.update()

    def toggle_opening_training(self):
        self.training_mode=not self.training_mode
        self.training_button.setText("Stop Opening Trainer" if self.training_mode else "Start Opening Trainer")
        if self.training_mode:
            self.training_message="Training: make a move from the current position"
        else:
            self.training_message="Training: off"
        self.training_status.setText(self.training_message)
        self.update_opening_explorer()

    def click_square(self,sq):
        self.hint_move = None; self.best_line_moves=[]
        if self.play_mode and self.turn != self.human_side:
            self.status.setText("Pikafish is thinking…")
            return
        piece=self.board.get(sq); side_upper=self.turn=="w"
        if self.selected is None:
            if piece and piece.isupper()==side_upper:
                self.selected=sq; self.status.setText(f"{len(legal_destinations(self.board,sq))} available moves"); self.board_widget.update()
            return
        if piece and piece.isupper()==side_upper:
            self.selected=sq; self.status.setText(f"{len(legal_destinations(self.board,sq))} available moves"); self.board_widget.update(); return
        if sq not in legal_destinations(self.board,self.selected): self.status.setText("That is not a legal move"); return
        self.make_move(self.selected,sq)

    def _prepare_move_sound(self):
        """Create the move sound once and return its path.

        A short silent pre-roll is included before the click.  On some Windows
        audio devices the first few milliseconds are lost while the output
        device wakes up; the pre-roll absorbs that delay so the first move is
        audible too.
        """
        try:
            # Use a new filename so an older cached WAV is not reused.
            path = os.path.join(tempfile.gettempdir(), "pikafish_move_v7.wav")
            if not os.path.exists(path):
                rate = 22050
                pre_roll = 0.090      # lets a sleeping Windows audio endpoint wake up
                click_duration = 0.075
                tail = 0.015
                pre_n = int(rate * pre_roll)
                click_n = int(rate * click_duration)
                tail_n = int(rate * tail)
                samples = [0] * pre_n
                for i in range(click_n):
                    t = i / rate
                    env = math.exp(-42 * t)
                    val = (math.sin(2*math.pi*620*t) + 0.45*math.sin(2*math.pi*930*t)) * env
                    samples.append(max(-32767, min(32767, int(val * 12500))))
                samples.extend([0] * tail_n)
                with wave.open(path, "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
                    wf.writeframes(b"".join(struct.pack("<h", x) for x in samples))
            return path
        except Exception:
            return None

    def _sound_worker(self):
        """Play queued move sounds one at a time so rapid moves are never dropped."""
        while True:
            self.sound_q.get()
            try:
                if os.name == "nt" and self.sound_path:
                    import winsound
                    # Synchronous playback is intentional here: this worker thread
                    # serialises clicks, while the GUI remains fully responsive.
                    winsound.PlaySound(self.sound_path, winsound.SND_FILENAME | winsound.SND_SYNC | winsound.SND_NODEFAULT)
                else:
                    QApplication.beep()
            except Exception:
                pass
            finally:
                self.sound_q.task_done()

    def play_move_sound(self):
        """Queue one click for every completed move."""
        self.sound_q.put_nowait(1)

    def _populate_move_list(self, all_moves=None, current_index=None):
        """Refresh the move list, optionally showing a full loaded game."""
        display_moves = list(all_moves if all_moves is not None else self.moves)
        self.movelist.blockSignals(True)
        self.movelist.clear()
        standard_start = self.engine_base_fen.split()[0] == ENGINE_START_FEN.split()[0]
        departure = opening_book_departure(display_moves, standard_start)
        departure_index = departure[0] if departure else None
        for i,mv in enumerate(display_moves, 1):
            review = self.review_results[i-1] if i-1 < len(self.review_results) else None
            suffix = f"   {review['label']}" if review else ""
            if departure_index == i:
                suffix += "   ← left book"
            self.movelist.addItem(f"{i:>3}. {mv}{suffix}")
        if current_index is None:
            current_index = len(self.moves)
        if current_index > 0 and self.movelist.count():
            self.movelist.setCurrentRow(min(current_index-1, self.movelist.count()-1))
            self.movelist.scrollToItem(self.movelist.currentItem())
        else:
            self.movelist.clearSelection(); self.movelist.setCurrentRow(-1)
        self.movelist.blockSignals(False)

    def _rebuild_position(self, move_count):
        """Recreate the board at a point in the currently loaded PGN."""
        if self.loaded_game_moves is None:
            return False
        move_count = max(0, min(int(move_count), len(self.loaded_game_moves)))
        board, turn = parse_fen(self.gui_base_fen)
        played = []
        for i, text in enumerate(self.loaded_game_moves[:move_count], 1):
            mv = parse_move(text)
            if not mv:
                raise ValueError(f"Move {i} is not a valid ICCS move: {text}")
            a,b = mv
            if a not in board:
                raise ValueError(f"Move {i} ({text}) has no piece on {square_name(a)}")
            board[b] = board.pop(a)
            played.append(text)
            turn = "b" if turn == "w" else "w"
        self.board, self.turn, self.moves = board, turn, played
        self.loaded_index = move_count
        self.history=[]; self.selected=None; self.hint_move=None; self.best_line_moves=[]; self.coach_hint_level=0
        self._populate_move_list(self.loaded_game_moves, self.loaded_index)
        self.analysis.clear(); self.eval_label.setText("Score: —    Depth: —")
        self.update_state_labels()
        if hasattr(self, "eval_graph"): self.eval_graph.update()
        return True

    def _leave_loaded_replay(self):
        """Turn the current replay position into a normal editable game."""
        if self.loaded_game_moves is not None:
            self.loaded_game_moves=None; self.loaded_index=len(self.moves); self.loaded_filename=""
            self._populate_move_list()

    def make_move(self,a,b,from_engine=False):
        self.stop_analysis(); self.hint_move=None; self.best_line_moves=[]
        # Capture book choices before the move so trainer feedback can judge the
        # move that was actually made.
        standard_start = self.engine_base_fen.split()[0] == ENGINE_START_FEN.split()[0]
        before_choices = opening_book_choices(self.moves, standard_start)
        move_text = square_name(a)+square_name(b)
        if self.retry_mode and not from_engine:
            if self._handle_retry_attempt(move_text):
                self.selected=None; self.board_widget.update()
                return
        # If the user branches from an imported game, keep the currently shown
        # position/moves and simply leave replay mode before adding the new move.
        self._leave_loaded_replay()
        self.history.append((dict(self.board),self.turn,list(self.moves)))
        self.board[b]=self.board.pop(a); self.moves.append(move_text); self.turn="b" if self.turn=="w" else "w"; self.selected=None
        self._populate_move_list(); self.status.setText("Pikafish move played" if from_engine and self.play_mode else "Engine move played" if from_engine else "Move played"); self.play_move_sound(); self.update_state_labels()
        if self.training_mode and not from_engine:
            if before_choices:
                allowed={m for m,_ in before_choices}
                if move_text in allowed:
                    desc=next((d for m,d in before_choices if m==move_text), "Book move")
                    self.training_message=f"Training: ✓ in book — {move_text} ({desc})"
                else:
                    expected=", ".join(m for m,_ in before_choices)
                    self.training_message=f"Training: left book with {move_text}; book choices were {expected}"
            else:
                self.training_message="Training: built-in book coverage ends here — move not judged"
            self.training_status.setText(self.training_message)
        # In play mode Pikafish replies automatically after the human move.
        # A short timer lets Qt finish repainting the human move first.
        if self.play_mode and not from_engine and self.turn != self.human_side:
            if not in_check(self.board, self.turn=="w") or not is_checkmate(self.board, self.turn=="w"):
                QTimer.singleShot(180, self.request_play_engine_move)

    def current_engine_fen(self):
        """Return the currently displayed position as a Pikafish-compatible Xiangqi FEN."""
        rows=[]
        piece_map=str.maketrans({"h":"n","H":"N","e":"b","E":"B","s":"p","S":"P"})
        for y in range(RANKS):
            empty=0; parts=[]
            for x in range(FILES):
                piece=self.board.get((x,y))
                if piece is None:
                    empty += 1
                else:
                    if empty:
                        parts.append(str(empty)); empty=0
                    parts.append(piece.translate(piece_map))
            if empty:
                parts.append(str(empty))
            rows.append("".join(parts))
        return "/".join(rows)+f" {self.turn} - - 0 1"

    def copy_fen(self):
        fen=self.current_engine_fen()
        QApplication.clipboard().setText(fen)
        self.status.setText("Current FEN copied to clipboard")

    def load_fen_dialog(self):
        current=self.current_engine_fen()
        text,ok=QInputDialog.getMultiLineText(self,"Load Xiangqi FEN","Paste Pikafish/Xiangqi FEN:",current)
        if not ok:
            return
        fen=" ".join(text.strip().split())
        if not fen:
            return
        try:
            parts=fen.split()
            if len(parts) < 2:
                raise ValueError("FEN must include both the board and side to move (w or b).")
            rows=parts[0].split("/")
            if len(rows) != 10:
                raise ValueError("A Xiangqi FEN must contain exactly 10 ranks.")
            allowed=set("rnbakcpRNBAKCP123456789")
            for row_no,row in enumerate(rows,1):
                if any(ch not in allowed for ch in row):
                    raise ValueError(f"Rank {row_no} contains an unsupported FEN character.")
                count=0
                for ch in row:
                    count += int(ch) if ch.isdigit() else 1
                if count != 9:
                    raise ValueError(f"Rank {row_no} describes {count} files; Xiangqi requires 9.")
            if parts[1] not in ("w","b"):
                raise ValueError("Side to move must be 'w' (Red) or 'b' (Black).")
            # Normalise optional fields so Pikafish always receives a complete FEN.
            engine_fen=parts[0]+f" {parts[1]} - - 0 1"
            gui_fen=engine_fen_to_gui(engine_fen)
            board,turn=parse_fen(gui_fen)
            if "K" not in board.values() or "k" not in board.values():
                raise ValueError("The position must contain both generals (K and k).")

            self.stop_analysis(); self.play_mode=False
            if hasattr(self,"play_button"):
                self.play_button.setText("Play Pikafish")
                self.play_side.setEnabled(True); self.play_level.setEnabled(True)
            self.engine_base_fen=engine_fen; self.gui_base_fen=gui_fen
            self.board,self.turn=board,turn
            self.moves=[]; self.history=[]; self.selected=None; self.hint_move=None; self.best_line_moves=[]
            self.loaded_game_moves=None; self.loaded_game_result="*"; self.loaded_index=0; self.loaded_filename=""
            self.review_moves=[]; self.review_results=[]
            self.review_summary.setText("Game review: not run")
            self.analysis.clear(); self.eval_label.setText("Score: —    Depth: —")
            self._populate_move_list()
            if hasattr(self,"eval_graph"):
                self.eval_graph.update()
            self.status.setText("FEN position loaded")
            self.update_state_labels()
        except Exception as ex:
            QMessageBox.critical(self,"Load FEN",f"Could not load this FEN:\n\n{ex}")

    def position_cmd(self): return "position fen "+self.engine_base_fen+(" moves "+" ".join(self.moves) if self.moves else "")
    def position_cmd_for_moves(self, moves):
        return "position fen "+self.engine_base_fen+(" moves "+" ".join(moves) if moves else "")
    def new_game(self):
        self.retry_mode=False; self.retry_expected=None
        if hasattr(self,"retry_button"): self.retry_button.setText("Retry Mistakes")
        self.stop_analysis(); self.hint_move=None; self.best_line_moves=[]; self.engine_base_fen=ENGINE_START_FEN; self.gui_base_fen=START_FEN
        self.loaded_game_moves=None; self.loaded_game_result="*"; self.loaded_index=0; self.loaded_filename=""
        self.review_results=[]; self.review_summary.setText("Game review: not run")
        if hasattr(self, "prev_mistake_button"):
            self.prev_mistake_button.setEnabled(False); self.next_mistake_button.setEnabled(False)
        if hasattr(self, "eval_graph"): self.eval_graph.update()
        self.board,self.turn=parse_fen(START_FEN); self.moves=[]; self.history=[]; self.selected=None; self.movelist.clear(); self.analysis.clear(); self.eval_label.setText("Score: —    Depth: —"); self.status.setText("New game")
        if self.training_mode:
            self.training_message="Training: new game — choose a book move"
            if hasattr(self,"training_status"): self.training_status.setText(self.training_message)
        self.update_state_labels()
    def undo(self):
        if self.loaded_game_moves is not None:
            self.pgn_back(); return
        if not self.history: return
        self.stop_analysis(); self.hint_move=None; self.best_line_moves=[]; self.board,self.turn,self.moves=self.history.pop(); self.selected=None; self._populate_move_list(); self.update_state_labels()
    def flip(self): self.flipped=not self.flipped; self.board_widget.update()
    def toggle_piece_style(self):
        self.piece_style="european" if self.piece_style=="chinese" else "chinese"; self.style_button.setText("Pieces: Symbols" if self.piece_style=="european" else "Pieces: Chinese"); self.update_state_labels()

    def update_captured(self):
        starting,_=parse_fen(self.gui_base_fen); s=Counter(starting.values()); cur=Counter(self.board.values()); labels=NAMES if self.piece_style=="chinese" else EURO_NAMES
        def missing(order):
            out=[]
            for piece in order: out.extend([labels[piece]]*max(0,s[piece]-cur[piece]))
            return "  ".join(out) if out else "None"
        self.captured_red.setText("Taken by Red: "+missing("rheacs")); self.captured_black.setText("Taken by Black: "+missing("RHEACS"))

    def load_pgn(self):
        filename,_=QFileDialog.getOpenFileName(self,"Load Xiangqi PGN","","PGN game files (*.pgn);;All files (*.*)")
        if not filename: return
        try:
            with open(filename,"r",encoding="utf-8-sig",errors="replace") as f:
                text=f.read()
            engine_fen,moves,result=parse_pgn_text(text)
            if not moves:
                raise ValueError("No ICCS coordinate moves (for example h2e2) were found in this PGN.")

            # Validate the full main line before replacing the current game.
            gui_fen=engine_fen_to_gui(engine_fen)
            board,turn=parse_fen(gui_fen)
            for i,text_move in enumerate(moves,1):
                mv=parse_move(text_move)
                if not mv:
                    raise ValueError(f"Move {i} is not a valid coordinate move: {text_move}")
                a,b=mv
                if a not in board:
                    raise ValueError(f"Move {i} ({text_move}) cannot be replayed: no piece is on {square_name(a)}.")
                board[b]=board.pop(a)
                turn="b" if turn=="w" else "w"

            self.stop_analysis(); self.hint_move=None; self.best_line_moves=[]
            self.engine_base_fen=engine_fen
            self.gui_base_fen=gui_fen
            self.loaded_game_moves=list(moves); self.loaded_game_result=result; self.loaded_filename=filename
            self.review_results=[]; self.review_summary.setText("Game review: not run")
            if hasattr(self, "eval_graph"): self.eval_graph.update()
            # Open the PGN at the end of the game. Back/Forward or clicking the
            # move list can then inspect any earlier position.
            self._rebuild_position(len(moves))
            self.status.setText(f"Loaded {os.path.basename(filename)} — {len(moves)} moves")
        except Exception as ex:
            QMessageBox.critical(self,"Load PGN",f"Could not load this PGN:\n\n{ex}")

    def pgn_back(self):
        if self.loaded_game_moves is None:
            self.status.setText("Load a PGN first to use Back/Forward")
            return
        if self.loaded_index <= 0:
            self.status.setText("At the start of the loaded game")
            return
        self.stop_analysis(); self._rebuild_position(self.loaded_index-1)
        self.status.setText(f"PGN position: move {self.loaded_index} of {len(self.loaded_game_moves)}")

    def pgn_forward(self):
        if self.loaded_game_moves is None:
            self.status.setText("Load a PGN first to use Back/Forward")
            return
        if self.loaded_index >= len(self.loaded_game_moves):
            self.status.setText("At the end of the loaded game")
            return
        self.stop_analysis(); self._rebuild_position(self.loaded_index+1)
        self.status.setText(f"PGN position: move {self.loaded_index} of {len(self.loaded_game_moves)}")

    def jump_to_pgn_item(self,item):
        if self.loaded_game_moves is None:
            return
        row=self.movelist.row(item)
        if row < 0: return
        try:
            self.stop_analysis(); self._rebuild_position(row+1)
            if row < len(self.review_results): self.show_coach_for_review(row)
            self.status.setText(f"PGN position: move {self.loaded_index} of {len(self.loaded_game_moves)}")
        except Exception as ex:
            QMessageBox.critical(self,"PGN navigation",str(ex))

    def save_pgn(self):
        checked_red=self.turn=="w"; result=("0-1" if checked_red else "1-0") if is_checkmate(self.board,checked_red) else "*"
        filename,_=QFileDialog.getSaveFileName(self,"Save Xiangqi PGN",f"xiangqi-{datetime.now().strftime('%Y-%m-%d')}.pgn","PGN game files (*.pgn);;All files (*.*)")
        if not filename: return
        if not filename.lower().endswith(".pgn"): filename += ".pgn"
        try:
            # If Review Game has analysed this exact main line, preserve the
            # Pikafish classifications, evaluations, best move and suggested PV
            # as standard PGN comments. Otherwise save a normal clean PGN.
            reviewed_ok = bool(self.review_results) and all(
                i < len(self.review_results) and
                str(self.review_results[i].get("move", "")).lower() == str(mv).lower()
                for i, mv in enumerate(self.moves)
            )
            pgn_text=(make_annotated_pgn(self.moves,self.review_results,result)
                      if reviewed_ok else make_pgn(self.moves,result))
            if self.engine_base_fen != ENGINE_START_FEN:
                pgn_text=pgn_text.replace(f'[FEN "{ENGINE_START_FEN}"]', f'[FEN "{self.engine_base_fen}"]')
            with open(filename,"w",encoding="utf8") as f: f.write(pgn_text)
            self.status.setText("Annotated PGN saved" if reviewed_ok else "PGN saved"); QMessageBox.information(self,"Save PGN",f"Game saved to:\n{filename}" + ("\n\nPikafish review comments were included." if reviewed_ok else ""))
        except OSError as ex: QMessageBox.critical(self,"Save PGN",str(ex))

    def connect_dialog(self):
        d=ConnectionDialog(self.cfg,self)
        if d.exec()!=QDialog.Accepted: return
        self.cfg=d.values(); self.save_cfg(); password=d.password.text(); self.status.setText("Connecting…")
        threading.Thread(target=self.do_connect,args=(password,),daemon=True).start()
    def do_connect(self,password):
        try:
            if not self.cfg.host.strip():
                raise RuntimeError("Enter the Raspberry Pi hostname or IP address.")
            if not self.cfg.username.strip():
                raise RuntimeError("Enter the SSH username.")
            if not self.cfg.engine.strip():
                raise RuntimeError("Enter the full path to the Pikafish executable on the remote system.")
            self.engine.connect(self.cfg,password); self.engine_q.put("@CONNECTED")
        except Exception as ex:
            self.engine_q.put("@ERROR "+str(ex))
    def hint_move_request(self):
        """Ask Pikafish for the best move and show it on the board without playing it."""
        try:
            self.stop_analysis()
            self.hint_move = None
            self.pending = "hint"
            self.engine.send("setoption name MultiPV value 1")
            self.engine.send(self.position_cmd())
            self.engine.send(f"go depth {self.cfg.depth}")
            self.status.setText("Pikafish is finding a hint…")
            self.board_widget.update()
        except Exception as ex:
            self.pending = None
            QMessageBox.critical(self,"Pikafish",str(ex))

    def _play_level_changed(self, text):
        self.play_difficulty = text
        if self.play_mode:
            self.status.setText(f"Playing Pikafish — {text}")

    def toggle_play_mode(self):
        """Start/stop an automatic game against Pikafish.

        The three levels are deliberately relative rather than advertised as
        Elo ratings because search depth depends on the Raspberry Pi and engine
        build.  Easy is shallow, Medium is a useful club-strength challenge,
        and Hard uses a longer search.
        """
        if self.play_mode:
            self.play_mode=False
            if self.pending == "play":
                try: self.engine.send("stop")
                except Exception: pass
                self.pending=None
            self.play_button.setText("Play Pikafish")
            self.play_side.setEnabled(True)
            self.play_level.setEnabled(True)
            self.status.setText("Play mode stopped")
            return

        if not self.engine.proc or self.engine.proc.poll() is not None:
            QMessageBox.information(self, "Play Pikafish", "Connect to Pikafish first, then press Play Pikafish.")
            return

        self.stop_analysis()
        self.play_mode=True
        self.human_side = "w" if self.play_side.currentText().endswith("Red") else "b"
        self.play_difficulty=self.play_level.currentText()
        self.play_button.setText("Stop Game")
        self.play_side.setEnabled(False)
        self.play_level.setEnabled(False)
        # Always start a clean standard game so the selected colour is clear.
        self.new_game()
        who = "Red" if self.human_side == "w" else "Black"
        self.status.setText(f"Playing Pikafish ({self.play_difficulty}) — you are {who}")
        if self.turn != self.human_side:
            QTimer.singleShot(250, self.request_play_engine_move)

    def request_play_engine_move(self):
        if not self.play_mode or self.turn == self.human_side:
            return
        try:
            self.pending="play"
            self.best_line_moves=[]
            self.engine.send("setoption name MultiPV value 1")
            self.engine.send(self.position_cmd())
            level=self.play_difficulty
            if level == "Easy":
                # Very shallow search: deliberately leaves tactical chances.
                self.engine.send("go depth 3")
            elif level == "Medium":
                self.engine.send("go depth 8")
            else:
                # Hard gets a meaningful think without making every move slow.
                self.engine.send("go movetime 3000")
            self.status.setText(f"Pikafish is thinking ({level})…")
        except Exception as ex:
            self.pending=None
            self.play_mode=False
            self.play_button.setText("Play Pikafish")
            self.play_side.setEnabled(True); self.play_level.setEnabled(True)
            QMessageBox.critical(self,"Pikafish",str(ex))

    def engine_move(self):
        try:
            self.pending="move"; self.engine.send("setoption name MultiPV value 1"); self.engine.send(self.position_cmd()); self.engine.send(f"go depth {self.cfg.depth}"); self.status.setText("Pikafish is thinking…")
        except Exception as ex: QMessageBox.critical(self,"Pikafish",str(ex))
    def toggle_analysis(self):
        if self.analysis_running:
            self.stop_analysis(); self.status.setText("Analysis stopped"); return
        try:
            seconds=int(self.analysis_time.currentText().split()[0])
            self.last_analysis_depth=None; self.best_line_moves=[]; self.multipv_lines={}
            self.pending="analysis"; self.analysis_running=True
            self.engine.send("setoption name MultiPV value 3")
            self.engine.send(self.position_cmd())
            self.engine.send(f"go movetime {seconds*1000}")
            self.status.setText(f"Analysing for {seconds} seconds…")
        except Exception as ex:
            self.analysis_running=False; QMessageBox.critical(self,"Pikafish",str(ex))
    def stop_analysis(self):
        busy = self.analysis_running or self.review_running
        if busy:
            try:
                self.engine.send("stop")
                self.engine.send("setoption name MultiPV value 1")
            except Exception: pass
        self.analysis_running=False
        if self.review_running:
            self.review_running=False
            self.review_phase=None
            if hasattr(self, "review_button"):
                self.review_button.setText("Review Game")
            if self.pending in ("review_before", "review_after"):
                self.pending=None

    def _score_from_info(self, score_match):
        if not score_match:
            return None
        kind, raw = score_match.group(1), int(score_match.group(2))
        if kind == "cp":
            return raw
        # Keep mate values comparable with centipawn scores for move-loss classification.
        return 100000 if raw > 0 else -100000

    def _classify_review_move(self, actual_move, best_move, before_score, after_score):
        if best_move and actual_move.lower() == best_move.lower():
            return "★ Best", 0
        if before_score is None or after_score is None:
            return "Unrated", None
        # Engine scores are from the side-to-move viewpoint.  After the played move
        # the opponent is to move, so negate that score to compare from the mover's side.
        mover_after = -after_score
        loss = max(0, before_score - mover_after)
        if loss <= 35:
            label = "Good"
        elif loss <= 90:
            label = "Inaccuracy"
        elif loss <= 180:
            label = "Mistake"
        else:
            label = "Blunder"
        return label, loss

    def toggle_game_review(self):
        if self.review_running:
            self.stop_analysis()
            self.status.setText("Game review stopped")
            return
        moves = list(self.loaded_game_moves if self.loaded_game_moves is not None else self.moves)
        if not moves:
            QMessageBox.information(self, "Review Game", "There are no moves to review yet. Load a PGN or play a game first.")
            return
        try:
            self.stop_analysis()
            self.review_seconds = int(self.review_time.currentText().split()[0])
            self.review_moves = moves
            self.review_results = []
            self.eval_graph.update()
            self.review_index = 0
            self.review_running = True
            self.review_button.setText("Stop Review")
            self.review_summary.setText(f"Game review: analysing 0/{len(moves)} moves…")
            self._populate_move_list(self.loaded_game_moves if self.loaded_game_moves is not None else self.moves,
                                     self.loaded_index if self.loaded_game_moves is not None else len(self.moves))
            self._start_review_before()
        except Exception as ex:
            self.review_running=False
            self.review_button.setText("Review Game")
            QMessageBox.critical(self,"Review Game",str(ex))

    def _start_review_before(self):
        if not self.review_running:
            return
        if self.review_index >= len(self.review_moves):
            self._finish_game_review()
            return
        self.last_engine_score=None; self.last_engine_pv=""; self.best_line_moves=[]; self.last_analysis_depth=None
        self.review_before_score=None; self.review_bestmove=None; self.review_before_pv=""
        self.review_phase="before"; self.pending="review_before"
        prefix=self.review_moves[:self.review_index]
        self.engine.send("setoption name MultiPV value 1")
        self.engine.send(self.position_cmd_for_moves(prefix))
        self.engine.send(f"go movetime {self.review_seconds*1000}")
        self.status.setText(f"Reviewing move {self.review_index+1} of {len(self.review_moves)}…")

    def _start_review_after(self):
        self.last_engine_score=None; self.last_engine_pv=""; self.best_line_moves=[]; self.last_analysis_depth=None
        self.review_phase="after"; self.pending="review_after"
        prefix=self.review_moves[:self.review_index+1]
        self.engine.send("setoption name MultiPV value 1")
        self.engine.send(self.position_cmd_for_moves(prefix))
        self.engine.send(f"go movetime {self.review_seconds*1000}")

    def _record_review_result(self, after_score):
        actual=self.review_moves[self.review_index]
        label, loss=self._classify_review_move(actual, self.review_bestmove, self.review_before_score, after_score)
        self.review_results.append({
            "move": actual, "label": label, "loss": loss,
            "best": self.review_bestmove, "before": self.review_before_score, "after": after_score,
            "pv": self.review_before_pv
        })
        self.review_index += 1
        self._populate_move_list(self.loaded_game_moves if self.loaded_game_moves is not None else self.moves,
                                 self.loaded_index if self.loaded_game_moves is not None else len(self.moves))
        self.review_summary.setText(f"Game review: analysed {self.review_index}/{len(self.review_moves)} moves…")
        self.eval_graph.update()
        self._start_review_before()

    def _finish_game_review(self):
        self.review_running=False; self.review_phase=None; self.pending=None
        self.review_button.setText("Review Game")

        # Show review totals separately for Red and Black so it is immediately
        # clear which side made each inaccuracy, mistake or blunder.
        _, base_turn = parse_fen(self.gui_base_fen)
        labels = ("Best", "Good", "Inaccuracy", "Mistake", "Blunder", "Unrated")
        colour_counts = {"Red": Counter(), "Black": Counter()}

        for i, result in enumerate(self.review_results):
            mover = base_turn if i % 2 == 0 else ("b" if base_turn == "w" else "w")
            colour = "Red" if mover == "w" else "Black"
            label = result["label"].replace("★ ", "")
            colour_counts[colour][label] += 1

        def colour_summary(colour):
            counts = colour_counts[colour]
            parts = [f"{key}: {counts[key]}" for key in labels if counts.get(key)]
            return "   •   ".join(parts) if parts else "No rated moves"

        standard_start = self.engine_base_fen.split()[0] == ENGINE_START_FEN.split()[0]
        departure=opening_book_departure(self.review_moves, standard_start)
        if departure:
            move_no, actual, choices=departure
            book_line=f"\nOpening book — left book at move {move_no}: {actual} (book: {', '.join(m for m,_ in choices)})"
        elif standard_start:
            book_line="\nOpening book — no definite departure inside built-in book coverage"
        else:
            book_line="\nOpening book — unavailable for custom starting FEN"
        self.review_summary.setText(
            "Game review\n"
            f"Red — {colour_summary('Red')}\n"
            f"Black — {colour_summary('Black')}" + book_line
        )
        self.status.setText(f"Game review complete — {len(self.review_results)} moves analysed")
        self._populate_move_list(self.loaded_game_moves if self.loaded_game_moves is not None else self.moves,
                                 self.loaded_index if self.loaded_game_moves is not None else len(self.moves))
        has_mistakes=any(r.get("label") in ("Mistake", "Blunder") for r in self.review_results)
        self.prev_mistake_button.setEnabled(has_mistakes); self.next_mistake_button.setEnabled(has_mistakes)
        retryable=any(r.get("label") in ("Mistake","Blunder") and (r.get("best") or r.get("bestmove")) for r in self.review_results)
        self.retry_button.setEnabled(retryable)
        self._record_review_statistics()
        self.eval_graph.update()

    def show_coach_for_review(self, index):
        if index < 0 or index >= len(self.review_results): return
        r=self.review_results[index]
        if r.get("label") not in ("Mistake","Blunder"):
            self.coach_record=None; self.coach_title.setText("Select a Mistake or Blunder."); self.coach_detail.setText(""); self.coach_prompt.setText("")
            for b in (self.coach_hint_button,self.coach_best_button,self.coach_continue_button): b.setEnabled(False)
            return
        _, base_turn=parse_fen(self.gui_base_fen)
        mover_turn=base_turn if index%2==0 else ("b" if base_turn=="w" else "w")
        side="Red" if mover_turn=="w" else "Black"; self.coach_record=(index,r); self.coach_hint_level=0
        def ev(v):
            try: return f"{float(v)/100:+.2f}"
            except Exception: return "—"
        best=r.get("bestmove") or r.get("best") or "—"
        self.coach_title.setText(f"{side} — {r.get('label','')}")
        self.coach_detail.setText(f"You played: <b>{r.get('move','—')}</b><br>Pikafish preferred: <b>{best}</b><br>Evaluation: <b>{ev(r.get('before'))} → {ev(r.get('after'))}</b>")
        self.coach_prompt.setText("<b>Try to find the better move yourself.</b>")
        for b in (self.coach_hint_button,self.coach_best_button,self.coach_continue_button): b.setEnabled(True)

    def coach_show_hint(self):
        """Progressive coaching hint. Only the final hint marks the board."""
        if not self.coach_record: return
        r=self.coach_record[1]; best=r.get("bestmove") or r.get("best")
        if not best: return
        try:
            a,b=parse_coord_move(best)
            piece=self.board.get(a)
            names={"r":"Rook","h":"Horse","e":"Elephant","a":"Advisor","k":"General","c":"Cannon","p":"Pawn"}
            piece_name=names.get(piece.lower(),"piece") if piece else "piece"
            self.coach_hint_level=min(self.coach_hint_level+1,3)
            if self.coach_hint_level == 1:
                self.coach_prompt.setText(f"<b>Hint 1:</b> Look for a move with one of your <b>{piece_name}s</b>.")
            elif self.coach_hint_level == 2:
                self.coach_prompt.setText(f"<b>Hint 2:</b> Look carefully at the <b>{piece_name} on {best[:2]}</b>.")
            else:
                # Third hint reveals the destination visually, but not the move text.
                self.board_widget.hint_arrow=(a,b); self.board_widget.update()
                self.coach_prompt.setText(f"<b>Hint 3:</b> The {piece_name} on <b>{best[:2]}</b> has a strong move to the highlighted square.")
        except Exception:
            self.coach_hint_level=min(self.coach_hint_level+1,3)
            self.coach_prompt.setText("<b>Hint:</b> Look for a stronger forcing or improving move in this position.")

    def coach_show_best(self):
        if not self.coach_record: return
        r=self.coach_record[1]; best=r.get("bestmove") or r.get("best")
        if not best: return
        try:
            a,b=parse_coord_move(best); self.board_widget.hint_arrow=(a,b); self.board_widget.update()
        except Exception: pass
        self.coach_prompt.setText(f"Best move: <b>{best}</b>")

    def coach_continue_analysis(self):
        if not self.coach_record: return
        self.coach_prompt.setText("Running Pikafish analysis for this position…")
        if not self.analysis_running: self.toggle_analysis()

    def _load_json_list(self, path):
        try:
            with open(path, "r", encoding="utf8") as f:
                data=json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_json_list(self, path, data):
        tmp=path+".tmp"
        with open(tmp, "w", encoding="utf8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)

    def save_interesting_position(self):
        """Store the current FEN locally with a user-supplied name."""
        opening, variation = recognize_opening(
            self.moves,
            self.engine_base_fen.split()[0] == ENGINE_START_FEN.split()[0]
        )
        default_name = opening if opening not in ("Starting position", "Unknown / unclassified") else "Interesting position"
        name, ok = QInputDialog.getText(self, "Save Position", "Position name:", text=default_name)
        if not ok or not name.strip():
            return
        records=self._load_json_list(self.positions_path)
        records.append({
            "name": name.strip(),
            "fen": self.current_engine_fen(),
            "opening": opening,
            "variation": variation,
            "saved_at": datetime.now().isoformat(timespec="seconds")
        })
        try:
            self._save_json_list(self.positions_path, records)
            self.status.setText(f"Saved position: {name.strip()}")
        except OSError as ex:
            QMessageBox.critical(self, "Save Position", f"Could not save the position:\n\n{ex}")

    def _review_mover_colour(self, index):
        _, base_turn=parse_fen(self.gui_base_fen)
        mover = base_turn if index % 2 == 0 else ("b" if base_turn == "w" else "w")
        return "Red" if mover == "w" else "Black"

    def _record_review_statistics(self):
        """Persist one compact statistics record for a completed review."""
        rated=[r for r in self.review_results if isinstance(r.get("loss"), (int,float))]
        avg_loss=(sum(r["loss"] for r in rated)/len(rated)) if rated else None
        counts={"Red":{"Mistake":0,"Blunder":0,"Inaccuracy":0},
                "Black":{"Mistake":0,"Blunder":0,"Inaccuracy":0}}
        for i,r in enumerate(self.review_results):
            label=r.get("label","").replace("★ ","")
            if label in ("Mistake","Blunder","Inaccuracy"):
                counts[self._review_mover_colour(i)][label] += 1
        opening, variation=recognize_opening(
            self.review_moves,
            self.engine_base_fen.split()[0] == ENGINE_START_FEN.split()[0]
        )
        result=self.loaded_game_result if self.loaded_game_result else "*"
        records=self._load_json_list(self.stats_path)
        records.append({
            "reviewed_at": datetime.now().isoformat(timespec="seconds"),
            "source": os.path.basename(self.loaded_filename) if self.loaded_filename else "GUI game",
            "moves": len(self.review_results),
            "average_loss_cp": avg_loss,
            "red": counts["Red"],
            "black": counts["Black"],
            "opening": opening,
            "variation": variation,
            "result": result
        })
        try:
            self._save_json_list(self.stats_path, records)
        except OSError:
            pass

    def show_game_statistics(self):
        records=self._load_json_list(self.stats_path)
        if not records:
            QMessageBox.information(self, "Game Statistics",
                                    "No reviewed-game statistics have been saved yet.\n\nRun Review Game to create the first entry.")
            return
        avgs=[r.get("average_loss_cp") for r in records if isinstance(r.get("average_loss_cp"), (int,float))]
        overall=(sum(avgs)/len(avgs))/100 if avgs else None
        red_m=sum(r.get("red",{}).get("Mistake",0) for r in records)
        red_b=sum(r.get("red",{}).get("Blunder",0) for r in records)
        black_m=sum(r.get("black",{}).get("Mistake",0) for r in records)
        black_b=sum(r.get("black",{}).get("Blunder",0) for r in records)

        # Opening performance here means engine accuracy by opening, rather than win-rate
        # alone, because many imported PGNs do not contain a decisive result.
        by_opening={}
        for r in records:
            op=r.get("opening") or "Unknown"
            val=r.get("average_loss_cp")
            if isinstance(val,(int,float)):
                by_opening.setdefault(op,[]).append(val)
        opening_lines=sorted(
            ((op, sum(vals)/len(vals)/100, len(vals)) for op,vals in by_opening.items()),
            key=lambda x:(-x[2], x[1])
        )[:5]

        trend="Not enough reviewed games yet"
        if len(avgs) >= 4:
            n=min(3, len(avgs)//2)
            early=sum(avgs[:n])/n/100
            recent=sum(avgs[-n:])/n/100
            delta=recent-early
            if delta < -0.05:
                trend=f"Improving: recent average loss is {abs(delta):.2f} better"
            elif delta > 0.05:
                trend=f"Recent average loss is {delta:.2f} higher"
            else:
                trend="Broadly stable"

        lines=[
            f"Reviewed games: {len(records)}",
            f"Average evaluation loss: {overall:.2f}" if overall is not None else "Average evaluation loss: —",
            "",
            f"Red — Mistakes: {red_m}   Blunders: {red_b}",
            f"Black — Mistakes: {black_m}   Blunders: {black_b}",
            "",
            f"Improvement trend: {trend}",
            "",
            "Opening performance (average eval loss):"
        ]
        if opening_lines:
            lines += [f"• {op}: {avg:.2f} across {n} review{'s' if n != 1 else ''}" for op,avg,n in opening_lines]
        else:
            lines.append("• No rated opening data yet")
        QMessageBox.information(self, "Game Statistics", "\n".join(lines))

    def toggle_retry_mistakes(self):
        if self.retry_mode:
            self.retry_mode=False; self.retry_expected=None
            self.retry_button.setText("Retry Mistakes")
            self.status.setText("Retry mistakes mode stopped")
            return
        bad=[i for i,r in enumerate(self.review_results) if r.get("label") in ("Mistake","Blunder") and (r.get("best") or r.get("bestmove"))]
        if not bad:
            QMessageBox.information(self, "Retry Mistakes", "Run Review Game first. No retryable Mistakes or Blunders are currently available.")
            return
        self.retry_mode=True; self.retry_indices=bad; self.retry_cursor=0
        self.retry_button.setText("Stop Retry")
        self._load_retry_position()

    def _load_retry_position(self):
        if not self.retry_mode:
            return
        if self.retry_cursor >= len(self.retry_indices):
            self.retry_mode=False; self.retry_expected=None
            self.retry_button.setText("Retry Mistakes")
            self.coach_prompt.setText("<b>Retry complete — you worked through every reviewed mistake/blunder.</b>")
            self.status.setText("Retry mistakes complete")
            return
        idx=self.retry_indices[self.retry_cursor]
        # Ensure there is a replayable main line and show the position BEFORE the mistake.
        if self.loaded_game_moves is None:
            self.loaded_game_moves=list(self.review_moves); self.loaded_game_result="*"; self.loaded_filename=""
        self.stop_analysis()
        self._rebuild_position(idx)
        self.show_coach_for_review(idx)
        rec=self.review_results[idx]
        self.retry_expected=(rec.get("best") or rec.get("bestmove") or "").lower()
        self.hint_move=None; self.best_line_moves=[]
        self.coach_prompt.setText(
            f"<b>Retry {self.retry_cursor+1} of {len(self.retry_indices)}:</b> "
            "find a better move. Make your move on the board."
        )
        self.status.setText(f"Retry mistake {self.retry_cursor+1}/{len(self.retry_indices)}")

    def _handle_retry_attempt(self, move_text):
        expected=(self.retry_expected or "").lower()
        if not expected:
            return False
        if move_text.lower() == expected:
            self.play_move_sound()
            self.coach_prompt.setText(f"<b>Correct — {move_text} is Pikafish's preferred move.</b>")
            self.status.setText("Correct move")
            self.retry_cursor += 1
            QTimer.singleShot(850, self._load_retry_position)
        else:
            self.coach_prompt.setText(
                f"<b>{move_text} is legal, but it isn't Pikafish's preferred move.</b> Try again, or use Show Hint."
            )
            self.status.setText("Try another move")
        return True

    def navigate_mistake(self, direction):
        """Jump cyclically between moves classified as Mistake or Blunder."""
        bad_positions=[i+1 for i,r in enumerate(self.review_results) if r.get("label") in ("Mistake", "Blunder")]
        if not bad_positions:
            self.status.setText("No mistakes or blunders found in the game review")
            return
        if self.loaded_game_moves is None:
            if not self.review_moves:
                return
            self.loaded_game_moves=list(self.review_moves); self.loaded_game_result="*"; self.loaded_filename=""
            current=0 if direction > 0 else len(self.loaded_game_moves)+1
        else:
            current=self.loaded_index
        if direction > 0:
            candidates=[p for p in bad_positions if p > current]
            target=candidates[0] if candidates else bad_positions[0]
        else:
            candidates=[p for p in bad_positions if p < current]
            target=candidates[-1] if candidates else bad_positions[-1]
        self.stop_analysis(); self._rebuild_position(target)
        result=self.review_results[target-1]
        self.show_coach_for_review(target-1)
        mover="Red" if ((parse_fen(self.gui_base_fen)[1] == "w") == ((target-1)%2 == 0)) else "Black"
        self.status.setText(f"{mover} {result['label']} at move {target}: {result['move']}")
        self.eval_graph.update()

    def evaluation_graph_points(self):
        """Return (position_index, centipawns from Red's viewpoint) from review data."""
        if not self.review_results:
            return []
        _, base_turn = parse_fen(self.gui_base_fen)
        raw_by_position = {}
        for i, result in enumerate(self.review_results):
            if result.get("before") is not None and i not in raw_by_position:
                raw_by_position[i] = result["before"]
            if result.get("after") is not None:
                raw_by_position[i+1] = result["after"]
        points=[]
        for pos, raw in sorted(raw_by_position.items()):
            side = base_turn if pos % 2 == 0 else ("b" if base_turn == "w" else "w")
            red_score = raw if side == "w" else -raw
            points.append((pos, red_score))
        return points

    def jump_to_graph_position(self, move_count):
        # For an imported PGN, jump directly.  For a game played in the GUI,
        # temporarily turn the reviewed main line into replay mode so graph clicks work too.
        if self.loaded_game_moves is None:
            if not self.review_moves:
                self.status.setText("Run Review Game first to use the evaluation graph")
                return
            self.loaded_game_moves=list(self.review_moves)
            self.loaded_game_result="*"
            self.loaded_filename=""
        self.stop_analysis()
        self._rebuild_position(move_count)
        self.status.setText(f"PGN position: move {self.loaded_index} of {len(self.loaded_game_moves)}")
        self.eval_graph.update()

    def show_analysis(self,line):
        depth=re.search(r"\bdepth (\d+)",line)
        score=re.search(r"\bscore (cp|mate) (-?\d+)",line)
        pv=re.search(r"\bpv (.+)$",line)
        mpv_match=re.search(r"\bmultipv (\d+)", line)
        mpv=int(mpv_match.group(1)) if mpv_match else 1
        if depth and mpv == 1:
            self.last_analysis_depth=int(depth.group(1))
        if score and mpv == 1:
            self.last_engine_score=self._score_from_info(score)
        if pv and mpv == 1:
            self.last_engine_pv=pv.group(1)

        # During normal Analyse, keep the latest line for each of Pikafish's top 3 choices.
        if self.pending == "analysis" and (depth or score or pv):
            entry=self.multipv_lines.get(mpv, {"depth":None,"score_text":"—","score_cp":None,"pv":""})
            if depth: entry["depth"]=int(depth.group(1))
            if score:
                entry["score_cp"]=self._score_from_info(score)
                entry["score_text"]=(f"{int(score.group(2))/100:+.2f}" if score.group(1)=="cp" else "Mate "+score.group(2))
            if pv: entry["pv"]=pv.group(1)
            self.multipv_lines[mpv]=entry

            # The board arrows always follow the best (MultiPV 1) continuation.
            best=self.multipv_lines.get(1, {})
            if best.get("pv"):
                parsed=[]
                for tok in best["pv"].split():
                    mv=parse_move(tok)
                    if mv: parsed.append(mv)
                    if len(parsed) >= 5: break
                self.best_line_moves=parsed
                self.board_widget.update()

            lines=["Top 3 moves"]
            for rank in (1,2,3):
                item=self.multipv_lines.get(rank)
                if not item or not item.get("pv"): continue
                tokens=item["pv"].split()
                first=tokens[0] if tokens else "—"
                d=item.get("depth") if item.get("depth") is not None else "—"
                lines.append(f"{rank}. {first}    {item.get('score_text','—')}    depth {d}")
                lines.append("   PV: "+item["pv"])
            self.analysis.setPlainText("\n".join(lines))
            best=self.multipv_lines.get(1)
            if best:
                self.eval_label.setText(f"Score: {best.get('score_text','—')}    Depth: {best.get('depth','—')}")
            return

        if pv and not self.review_running:
            parsed=[]
            for tok in pv.group(1).split():
                mv=parse_move(tok)
                if mv: parsed.append(mv)
                if len(parsed) >= 5: break
            self.best_line_moves=parsed
            self.board_widget.update()
        if self.review_running:
            return
        if depth or score:
            score_text="—"
            if score: score_text=f"{int(score.group(2))/100:+.2f}" if score.group(1)=="cp" else "Mate "+score.group(2)
            shown_depth=depth.group(1) if depth else (str(self.last_analysis_depth) if self.last_analysis_depth is not None else "—")
            self.eval_label.setText(f"Score: {score_text}    Depth: {shown_depth}")
        if pv: self.analysis.setPlainText("Principal variation\n\n"+pv.group(1))
    def poll_engine(self):
        try:
            while True:
                line=self.engine_q.get_nowait()
                if line=="@CONNECTED": self.status.setText("Connected to Pikafish")
                elif line.startswith("@ERROR "): self.status.setText("Connection failed"); QMessageBox.critical(self,"Connection",line[7:])
                elif line.startswith("info "): self.show_analysis(line)
                elif line.startswith("bestmove"):
                    if self.pending=="move":
                        mv=parse_move(line)
                        if mv and mv[0] in self.board: self.make_move(*mv,from_engine=True)
                    elif self.pending=="play":
                        mv=parse_move(line)
                        self.pending=None
                        if self.play_mode and mv and mv[0] in self.board:
                            self.make_move(*mv,from_engine=True)
                            if self.play_mode:
                                self.status.setText(f"Your move — {'Red' if self.human_side=='w' else 'Black'} ({self.play_difficulty})")
                        elif self.play_mode:
                            self.status.setText("Pikafish has no move")
                    elif self.pending=="hint":
                        mv=parse_move(line)
                        if mv and mv[0] in self.board:
                            self.hint_move = mv
                            self.status.setText(f"Hint: {square_name(mv[0])} → {square_name(mv[1])}")
                            self.board_widget.update()
                        else:
                            self.status.setText("No hint available")
                    elif self.pending=="analysis":
                        try: self.engine.send("setoption name MultiPV value 1")
                        except Exception: pass
                        self.status.setText(f"Analysis complete — depth {self.last_analysis_depth}" if self.last_analysis_depth is not None else "Analysis complete")
                    elif self.pending=="review_before":
                        m=re.search(r"\bbestmove\s+([a-i][0-9][a-i][0-9])", line, flags=re.I)
                        self.review_bestmove=m.group(1).lower() if m else None
                        self.review_before_score=self.last_engine_score
                        self.review_before_pv=self.last_engine_pv
                        actual=self.review_moves[self.review_index] if self.review_index < len(self.review_moves) else ""
                        # Analyse the resulting position even when the move matches bestmove.
                        # This gives the evaluation graph a score for every position.
                        self.pending=None
                        self._start_review_after()
                        continue
                    elif self.pending=="review_after":
                        after_score=self.last_engine_score
                        self.pending=None
                        self._record_review_result(after_score)
                        continue
                    self.pending=None; self.analysis_running=False
        except queue.Empty: pass
    def flash_check(self):
        self.check_flash_on=not self.check_flash_on
        if in_check(self.board,self.turn=="w"): self.board_widget.update()
    def closeEvent(self,event):
        self.stop_analysis(); self.engine.close(); event.accept()


def main():
    app=QApplication([])
    app.setStyle("Fusion")
    w=MainWindow(); w.show()
    app.exec()

if __name__ == "__main__":
    main()
