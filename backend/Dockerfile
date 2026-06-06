# KINYO B2B 查價系統後端 — Cloud Run 映像
# Python 3.12 slim；uvicorn 綁定 Cloud Run 注入的 $PORT
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先裝依賴（善用 Docker layer cache）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製應用程式原始碼
# 注意：.env / serviceAccountKey.json 由 .gcloudignore 排除，不會進到 build context
COPY . .

# Cloud Run 會以環境變數注入 PORT（本機 docker run 時預設 8080）
ENV PORT=8080
EXPOSE 8080

# shell 形式以展開 ${PORT}；exec 讓 uvicorn 直接接收 SIGTERM（優雅關閉）
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
