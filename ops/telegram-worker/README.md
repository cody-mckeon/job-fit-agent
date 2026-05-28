# Telegram command Cloudflare Worker

This Worker is the Phase 2 serverless Telegram command bridge. It receives Telegram bot webhook updates, validates that the update is from Cody's allowed chat, allowlists only job status commands, and triggers GitHub Actions with `repository_dispatch`.

No always-on VPS or bot server is required.

## Required Worker secrets

Set these with `wrangler secret put <NAME>`:

- `TELEGRAM_BOT_TOKEN` - bot token used when setting the webhook; the Worker does not return or expose it.
- `TELEGRAM_ALLOWED_CHAT_ID` - Cody's Telegram chat id; all other chats are ignored.
- `TELEGRAM_WEBHOOK_SECRET` - random secret sent by Telegram in `X-Telegram-Bot-Api-Secret-Token`.
- `GITHUB_OWNER` - GitHub repository owner.
- `GITHUB_REPO` - GitHub repository name.
- `GITHUB_DISPATCH_TOKEN` - GitHub token with permission to call `POST /repos/{owner}/{repo}/dispatches`.

## Security model

The Worker must:

1. Validate `X-Telegram-Bot-Api-Secret-Token` before reading/dispatching a command.
2. Validate `message.chat.id` equals `TELEGRAM_ALLOWED_CHAT_ID`.
3. Allow only supported commands: `applied`, `/applied`, `mark applied`, `skip`, `/skip`, `save`, `/save`.
4. Reject or ignore every other Telegram message.
5. Never include the GitHub token or Telegram token in HTTP responses.

## Supported Telegram messages

```text
applied 19
/applied 19
mark applied 19
skip 19 Not US eligible
/skip 19 Not US eligible
save 19
/save 19
```

The Worker forwards the original Telegram text to GitHub as:

```json
{
  "event_type": "job_status_command",
  "client_payload": {
    "command_text": "applied 19",
    "chat_id": "123456"
  }
}
```

## Deploy and set Telegram webhook

1. Create a Cloudflare Worker and copy `worker.js` into it, or deploy this directory with Wrangler.
2. Add all Worker secrets listed above.
3. Deploy the Worker and note the public HTTPS URL.
4. Set the Telegram webhook with a secret token:

   ```bash
   curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -F "url=https://<worker-subdomain>.workers.dev" \
     -F "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
   ```

5. Send `applied 19` in Telegram.
6. Verify the GitHub Actions **Job Status Command** workflow starts from the `repository_dispatch` event.
7. Verify Telegram receives the confirmation after the workflow updates the job database.

Telegram supports bot webhooks for receiving updates over HTTPS. GitHub `repository_dispatch` is the external event this bridge uses to trigger a workflow run inside the repository.
