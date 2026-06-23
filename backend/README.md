# WithDev Backend

WithDev の FastAPI バックエンドです。

## MVP

初期バックエンドでは、議事録生成APIを提供します。

- `POST /api/minutes/from-text`
- Firebase ID Tokenによる認証が必要です。
- リクエストボディには`text`を指定します。
- レスポンスの`minutes.body`にMarkdown形式の議事録を返します。

## セットアップ

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
```

以下の環境変数を設定します。

```bash
GEMINI_API_KEY=...
GEMINI_MODEL=...
CORS_ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
```

Firebase Admin SDKは、ローカル環境ではApplication Default Credentialsを使用します。必要に応じて`GOOGLE_APPLICATION_CREDENTIALS`を設定してください。

## 起動

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

ヘルスチェック:

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
