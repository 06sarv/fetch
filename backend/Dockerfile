FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/fetch
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn[standard]>=0.30,<1" "pydantic>=2.8,<3" "pandas>=2.2,<3" "numpy>=1.26,<3" "openpyxl>=3.1,<4" "httpx>=0.27,<1" "python-dotenv>=1.0,<2" "langgraph>=0.2,<2" "python-multipart>=0.0.9,<1"
COPY app ./app
RUN useradd --create-home --uid 10001 fetch && mkdir -p /var/lib/fetch && chown -R fetch:fetch /var/lib/fetch
USER fetch
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
