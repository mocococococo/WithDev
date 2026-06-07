# WithDev Backend

FastAPI backend for WithDev.

## MVP

The initial backend provides a minutes generation API.

- `POST /api/minutes/from-text`
- Firebase ID Token authentication is required.
- The request body uses `text`.
- The response returns Markdown minutes in `minutes.body`.

## Setup

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
```

Set these environment variables:

```bash
GEMINI_API_KEY=...
GEMINI_MODEL=...
CORS_ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
```

Firebase Admin SDK uses Application Default Credentials locally. Set `GOOGLE_APPLICATION_CREDENTIALS` when needed.

## Run

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## API

```http
POST /api/minutes/from-text
Authorization: Bearer <Firebase ID Token>
Content-Type: application/json
```

```json
{
  "text": "会議の文字起こし本文"
}
```

```json
{
  "minutes": {
    "body": "Markdown形式の議事録本文"
  }
}
```
