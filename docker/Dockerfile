FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# discord.py の音声送信に ffmpeg が必要
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src

# 読み上げ設定の DB を置くディレクトリ。空の named volume 初回マウント時に
# この所有権が引き継がれ、非 root の botuser でも書き込めるようにする
RUN useradd -m botuser \
    && mkdir -p /app/data \
    && chown -R botuser:botuser /app/data
USER botuser

CMD ["python", "-m", "src.main"]