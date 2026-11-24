FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 산출물 DOCX·PDF 내보내기: Pandoc(Markdown → DOCX), LibreOffice(DOCX → PDF), 한글 글꼴
RUN apt-get update \
    && apt-get install -y --no-install-recommends pandoc libreoffice-writer-nogui fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check

COPY . .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]