# Gemini App Compose

Wraps `gemini.google.com` (latest free Gemini model) via Camoufox and strips
watermarks from generated images before returning them.

```
Your app → watermark.chatflow.tech (watermark-remover proxy)
               ↓
         gemini-app.chatflow.tech (webai-2api)
               ↓
         gemini.google.com (Camoufox browser session)
```

## Deploy

### 1. Create GitHub repo and push
```bash
git init && git add . && git commit -m "init"
git remote add origin https://github.com/arbaaz04/gemini-app-compose.git
git push -u origin main
```

### 2. In Dokploy — create Compose service
- Project: "ai shopify app"
- Add Service → Compose → connect `arbaaz04/gemini-app-compose`
- Service name: `webai-2api`

### 3. Add environment variables
```
GEMINI_APP_DOMAIN=gemini-app.chatflow.tech
WATERMARK_DOMAIN=gemini.chatflow.tech
WEBAI_API_KEY=sk-change-me-to-your-secure-key
```

### 4. Add DNS records
Point both domains at your VPS IP.

### 5. Deploy

### 6. Sign in to Gemini
1. Open `https://gemini-app.chatflow.tech/webui`
2. Go to Workers → click the Login button next to `default`
3. A browser window opens (Camoufox)
4. Sign into your Google account on `gemini.google.com`
5. Done — the worker shows as "idle/ready"

### 7. Configure the worker for Gemini image generation
In `https://gemini-app.chatflow.tech/webui` → edit the default worker:
- Change `type` from `lmarena` to `gemini` (for images) or `gemini_text` (for text)
- Save and restart the worker

## API Usage

Call `https://gemini.chatflow.tech` — same OpenAI-compatible format:

```bash
# Image generation
curl https://gemini.chatflow.tech/v1/chat/completions \
  -H "Authorization: Bearer sk-your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini",
    "messages": [{"role": "user", "content": "a red panda in a forest"}]
  }'

# Health check
curl https://gemini.chatflow.tech/health
```

Images come back as base64 in the response with watermarks already removed.

## Config

WebAI2API config lives in the `webai-data` Docker volume at `/app/data/config.yaml`.
Edit it through the WebUI or by modifying the volume directly.
The API key is set under `server.auth` in that config file.
