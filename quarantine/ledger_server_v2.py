"""
Ledger desktop server — v2.
Pipeline audio: .m4a → Whisper (locale) → Claude API → {amount, category, confidence}

Requirements:
    pip install fastapi uvicorn zeroconf sqlalchemy pydantic \
                faster-whisper anthropic python-multipart aiofiles
"""

import hashlib
import json
import os
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import anthropic
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Numeric, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

# ── Config ────────────────────────────────────────────────────────────────────

DB_URL      = "sqlite:///ledger.db"
AUDIO_DIR   = Path("audio")
PORT        = 8765
SVC_TYPE    = "_ledger._tcp.local."
SVC_NAME    = f"DesktopLedger.{SVC_TYPE}"

# Whisper: "tiny" / "base" / "small" / "medium" — tradeoff velocità/accuratezza
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")

# Claude model
CLAUDE_MODEL = "claude-sonnet-4-20250514"

CATEGORIES = [
    "cibo", "trasporti", "salute", "lavoro",
    "casa", "intrattenimento", "shopping", "altro",
]

EXTRACTION_PROMPT = """\
Sei un assistente per la gestione delle spese personali.
Dato il testo trascritto da un memo vocale, estrai le seguenti informazioni in JSON:

- amount: importo numerico float (null se non menzionato o ambiguo)
- category: una delle seguenti categorie: {categories}
- confidence: float 0.0-1.0 che indica la tua certezza sull'estrazione

Regole:
- amount deve essere un numero positivo (es. 12.50), mai una stringa
- Se l'importo è ambiguo o assente, restituisci null
- confidence bassa (< 0.5) se il testo è poco chiaro o rumoroso
- Rispondi ESCLUSIVAMENTE con JSON valido, nessun testo aggiuntivo

Testo trascritto: "{transcript}"
""".format(categories=", ".join(CATEGORIES))


# ── DB ────────────────────────────────────────────────────────────────────────

AUDIO_DIR.mkdir(exist_ok=True)
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})

@event.listens_for(engine, "connect")
def _set_pragma(conn, _):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"

    id          = Column(String,          primary_key=True, default=lambda: str(uuid.uuid4()))
    date        = Column(String,          nullable=False)
    input_type  = Column(String,          nullable=False)          # 'text' | 'audio'
    description = Column(Text,            nullable=True)
    audio_path  = Column(String,          nullable=True)
    transcript  = Column(Text,            nullable=True)
    amount      = Column(Numeric(12, 2),  nullable=True)           # nullable per audio
    category    = Column(String,          nullable=True)
    confidence  = Column(Numeric(4, 3),   nullable=True)
    llm_raw     = Column(Text,            nullable=True)           # JSON grezzo audit
    sha256      = Column(String(64),      unique=True, nullable=False)
    created_at  = Column(DateTime,        default=datetime.utcnow)


Base.metadata.create_all(engine)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TxTextIn(BaseModel):
    id:          str
    date:        str
    description: str
    amount:      float
    sha256:      str


class TxOut(BaseModel):
    id:          str
    date:        str
    input_type:  str
    description: Optional[str]
    audio_path:  Optional[str]
    transcript:  Optional[str]
    amount:      Optional[float]
    category:    Optional[str]
    confidence:  Optional[float]
    sha256:      str
    created_at:  datetime

    class Config:
        from_attributes = True


class TxPatch(BaseModel):
    amount:   Optional[float] = None
    category: Optional[str]   = None


# ── ML models (lazy singleton) ────────────────────────────────────────────────

_whisper: Optional[WhisperModel]   = None
_claude:  Optional[anthropic.Anthropic] = None


def get_whisper() -> WhisperModel:
    global _whisper
    if _whisper is None:
        _whisper = WhisperModel(
            WHISPER_MODEL_SIZE,
            device="auto",           # cuda se disponibile, cpu altrimenti
            compute_type="int8",
        )
    return _whisper


def get_claude() -> anthropic.Anthropic:
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic()   # legge ANTHROPIC_API_KEY dall'env
    return _claude


# ── Audio pipeline ────────────────────────────────────────────────────────────

def transcribe(path: Path) -> str:
    segments, _ = get_whisper().transcribe(str(path), language="it", beam_size=5)
    return " ".join(s.text.strip() for s in segments).strip()


def extract_structured(transcript: str) -> dict:
    """Returns {amount, category, confidence, llm_raw}."""
    prompt = EXTRACTION_PROMPT.replace("{transcript}", transcript)
    msg = get_claude().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # estrai il primo blocco JSON presente nel testo (fallback)
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(m.group()) if m else {}

    return {
        "amount":     parsed.get("amount"),
        "category":   parsed.get("category"),
        "confidence": parsed.get("confidence"),
        "llm_raw":    raw,
    }


def audio_sha256(date: str, file_bytes: bytes) -> str:
    content_hash = hashlib.sha256(file_bytes).hexdigest()
    return hashlib.sha256(f"{date}|{content_hash}".encode()).hexdigest()


# ── mDNS ─────────────────────────────────────────────────────────────────────

def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


_azc: Optional[AsyncZeroconf] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _azc
    ip   = _local_ip()
    _azc = AsyncZeroconf()
    info = ServiceInfo(
        SVC_TYPE, SVC_NAME,
        addresses=[socket.inet_aton(ip)],
        port=PORT,
        properties={"v": "2"},
    )
    await _azc.async_register_service(info)
    print(f"[mDNS]    {SVC_NAME} @ {ip}:{PORT}")
    print(f"[Whisper] model={WHISPER_MODEL_SIZE}")
    yield
    await _azc.async_unregister_service(info)
    await _azc.async_close()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/transactions", response_model=dict)
def upsert_text(transactions: List[TxTextIn]):
    inserted = skipped = 0
    with Session(engine) as s:
        for tx in transactions:
            if s.query(Transaction).filter_by(sha256=tx.sha256).first():
                skipped += 1
                continue
            s.add(Transaction(
                id=tx.id, date=tx.date,
                input_type="text", description=tx.description,
                amount=tx.amount, sha256=tx.sha256,
            ))
            inserted += 1
        s.commit()
    return {"inserted": inserted, "skipped": skipped}


@app.post("/transactions/audio", response_model=dict)
async def upsert_audio(
    id:    Form(...),
    date:  Form(...),
    file:  UploadFile = File(...),
):
    raw_bytes = await file.read()
    sha = audio_sha256(date, raw_bytes)

    with Session(engine) as s:
        if s.query(Transaction).filter_by(sha256=sha).first():
            return {"inserted": 0, "skipped": 1}

    # Persist audio file
    dest = AUDIO_DIR / f"{id}.m4a"
    dest.write_bytes(raw_bytes)

    # Pipeline: transcribe → LLM extraction (sincrono, in background accettabile)
    try:
        transcript = transcribe(dest)
        extracted  = extract_structured(transcript)
    except Exception as e:
        # Non bloccare il sync se la pipeline fallisce
        transcript = None
        extracted  = {"amount": None, "category": None, "confidence": None, "llm_raw": str(e)}

    with Session(engine) as s:
        s.add(Transaction(
            id=id, date=date,
            input_type="audio",
            audio_path=str(dest),
            transcript=transcript,
            amount=extracted["amount"],
            category=extracted["category"],
            confidence=extracted["confidence"],
            llm_raw=extracted["llm_raw"],
            sha256=sha,
        ))
        s.commit()

    return {
        "inserted":   1,
        "skipped":    0,
        "transcript": transcript,
        "amount":     extracted["amount"],
        "category":   extracted["category"],
        "confidence": extracted["confidence"],
    }


@app.patch("/transactions/{tx_id}", response_model=dict)
def patch(tx_id: str, body: TxPatch):
    with Session(engine) as s:
        tx = s.query(Transaction).filter_by(id=tx_id).first()
        if not tx:
            raise HTTPException(404, "Transaction not found")
        if body.amount   is not None: tx.amount   = body.amount
        if body.category is not None: tx.category = body.category
        s.commit()
    return {"updated": tx_id}


@app.get("/transactions", response_model=List[TxOut])
def list_transactions(
    from_date:  Optional[str] = None,
    to_date:    Optional[str] = None,
    unreviewed: bool          = False,
):
    with Session(engine) as s:
        q = s.query(Transaction)
        if from_date:   q = q.filter(Transaction.date >= from_date)
        if to_date:     q = q.filter(Transaction.date <= to_date)
        if unreviewed:  q = q.filter(Transaction.amount == None)  # noqa: E711
        return q.order_by(Transaction.date.desc()).all()


@app.get("/ping")
def ping():
    return {"status": "ok", "version": "2"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
