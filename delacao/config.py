import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DP_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "data.db"

HOST = "127.0.0.1"
PORT = 8746

POLL_S = 5            # intervalo de amostragem
NO_DATA_GAP_S = 90    # buraco entre amostras = máquina suspensa/desligada
IDLE_AFTER_S = 180    # sem teclado/mouse por 3 min = ocioso
MIGALHA_S = 300       # trabalho contíguo < 5 min é Migalha
STITCH_S = 900        # Lacuna <= 15 min é emendada ao bloco anterior

RETENTION_DAYS = 30   # amostras de dias aprovados são purgadas após N dias (0 = nunca)
PURGE_EVERY_S = 3600  # frequência da purga, dentro do loop do coletor

DEFAULT_MODEL = "google/gemini-2.5-flash-lite"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Título de janela que indica chamada ativa (Meet/Teams); precedência sobre ociosidade
DEFAULT_CALL_PATTERNS = [
    r"\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b",   # código de sala do Google Meet
    r"(?i)^meet\b",
    r"(?i)(chamada|reuni[ãa]o|meeting).*microsoft teams",
]
