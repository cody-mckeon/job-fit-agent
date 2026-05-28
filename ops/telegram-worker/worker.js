const GITHUB_API_VERSION = "2022-11-28";
const ALLOWED_COMMAND = /^(?:\/?applied\s+\d+|mark\s+applied\s+\d+|\/?skip\s+\d+\s+.+|\/?save\s+\d+)$/i;
const DANGEROUS_SHELL_CHARS = /[;&|`$<>\\\r\n]/;

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function isAllowedCommand(text) {
  const normalized = String(text || "").trim().replace(/\s+/g, " ");
  return normalized && !DANGEROUS_SHELL_CHARS.test(normalized) && ALLOWED_COMMAND.test(normalized);
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return jsonResponse({ ok: true });
    }

    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
    if (!env.TELEGRAM_WEBHOOK_SECRET || secret !== env.TELEGRAM_WEBHOOK_SECRET) {
      return jsonResponse({ ok: false, error: "unauthorized" }, 401);
    }

    let update;
    try {
      update = await request.json();
    } catch (_error) {
      return jsonResponse({ ok: false, error: "invalid update" }, 400);
    }

    const message = update.message || update.edited_message || {};
    const text = String(message.text || "").trim();
    const chatId = message.chat && message.chat.id !== undefined ? String(message.chat.id) : "";

    if (!env.TELEGRAM_ALLOWED_CHAT_ID || chatId !== String(env.TELEGRAM_ALLOWED_CHAT_ID)) {
      return jsonResponse({ ok: true, ignored: true });
    }

    if (!isAllowedCommand(text)) {
      return jsonResponse({ ok: true, ignored: true });
    }

    const owner = env.GITHUB_OWNER;
    const repo = env.GITHUB_REPO;
    const dispatchUrl = `https://api.github.com/repos/${owner}/${repo}/dispatches`;
    const response = await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "job-fit-agent-telegram-worker",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        event_type: "job_status_command",
        client_payload: {
          command_text: text,
          chat_id: chatId,
        },
      }),
    });

    if (!response.ok) {
      return jsonResponse({ ok: false, error: "dispatch failed" }, 502);
    }

    return jsonResponse({ ok: true });
  },
};
