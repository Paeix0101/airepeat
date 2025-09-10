import os
import time
import threading
from flask import Flask, request
import requests

TOKEN = os.environ.get("BOT_TOKEN")  # Bot token from BotFather
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # Render URL + /webhook
BOT_API = f"https://api.telegram.org/bot{TOKEN}"

OWNER_ID = 8141547148  # Main Owner with full control
MONITOR_ID = 7514171886  # This user only receives new join/message user IDs

app = Flask(__name__)

repeat_jobs = {}
groups_file = "groups.txt"
media_groups = {}  # (chat_id, media_group_id) -> list of media dicts


# -------------------- Helper Functions -------------------- #
def send_message(chat_id, text, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return requests.post(f"{BOT_API}/sendMessage", json=payload)


def delete_message(chat_id, message_id):
    try:
        requests.post(f"{BOT_API}/deleteMessage", json={
            "chat_id": chat_id,
            "message_id": message_id
        })
    except:
        pass


# -------------------- Repeater -------------------- #
def repeater(chat_id, content, interval, job_ref, is_album=False):
    last_message_ids = []

    while job_ref.get("running", False):
        # delete last cycle
        for mid in last_message_ids:
            delete_message(chat_id, mid)
        last_message_ids = []

        try:
            if is_album:
                resp = requests.post(f"{BOT_API}/sendMediaGroup", json={
                    "chat_id": chat_id,
                    "media": content
                })
                if resp.ok and resp.json().get("ok"):
                    last_message_ids = [m["message_id"] for m in resp.json()["result"]]
            else:
                if "text" in content:
                    resp = send_message(chat_id, content["text"], parse_mode="HTML")
                    if resp.ok:
                        last_message_ids = [resp.json()["result"]["message_id"]]
                elif "photo" in content:
                    resp = requests.post(f"{BOT_API}/sendPhoto", json={
                        "chat_id": chat_id,
                        "photo": content["photo"],
                        "caption": content.get("caption", "")
                    })
                    if resp.ok:
                        last_message_ids = [resp.json()["result"]["message_id"]]
                elif "video" in content:
                    resp = requests.post(f"{BOT_API}/sendVideo", json={
                        "chat_id": chat_id,
                        "video": content["video"],
                        "caption": content.get("caption", "")
                    })
                    if resp.ok:
                        last_message_ids = [resp.json()["result"]["message_id"]]
        except Exception as e:
            print("Repeater error:", e)

        time.sleep(interval)


# -------------------- Webhook -------------------- #
@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json()
    msg = update.get("message") or update.get("channel_post")

    if not msg:
        return "OK"

    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    from_user = msg.get("from", {"id": None})

    # --- START COMMAND ---
    if text.strip().lower() == "/start":
        start_message = (
            "🤖 <b>REPEAT MESSAGES BOT</b>\n\n"
            "<b>📌 YOU CAN REPEAT MULTIPLE MESSAGES 📌</b>\n\n"
            "🔧📌 𝗔𝗗𝗩𝗔𝗡𝗖𝗘 𝗙𝗘𝗔𝗧𝗨𝗥𝗘 : -📸 𝗜𝗠𝗔𝗚𝗘 𝗔𝗟𝗕𝗨𝗠 <b>AND</b>🎬 𝗩𝗜𝗗𝗘𝗢 𝗔𝗟𝗕𝗨𝗠 <b>WITH AND WITHOUT CAPTION CAN BE REPEATED </b>\n\n"
            "This bot repeats 📹 Videos, 📝 Text, 🖼 Images, 🔗 Links, Albums (multiple images/videos) "
            "in intervals of <b>1 minute</b>, <b>3 minutes</b>, or <b>5 minutes</b>.\n\n"
            "📌It also deletes the last repeated message(s) before sending new one(s).\n\n"
            "🛠 <b>Commands:</b>\n\n"
            "🔹 /repeat1min - Reply to any message (or album) to repeat every 1 minute\n"
            "🔹 /repeat3min - Reply to any message (or album) to repeat every 3 minutes\n"
            "🔹 /repeat5min - Reply to any message (or album) to repeat every 5 minutes\n"
            "🔹 /stop - Send this to stop all repeating messages \n"
            "⚠️ Only <b>admins</b> can control this bot."
        )
        send_message(chat_id, start_message, parse_mode="HTML")
        return "OK"

    # --- CAPTURE ALBUM FILE_IDS ---
    if "media_group_id" in msg:
        mgid = msg["media_group_id"]
        media_groups.setdefault((chat_id, mgid), [])
        if "photo" in msg:
            media_groups[(chat_id, mgid)].append({
                "type": "photo",
                "media": msg["photo"][-1]["file_id"],
                "caption": msg.get("caption")
            })
        elif "video" in msg:
            media_groups[(chat_id, mgid)].append({
                "type": "video",
                "media": msg["video"]["file_id"],
                "caption": msg.get("caption")
            })

    # --- REPEAT COMMANDS ---
    if "reply_to_message" in msg and text.startswith("/repeat"):
        replied_msg = msg["reply_to_message"]

        if text.startswith("/repeat1min"):
            interval = 60
        elif text.startswith("/repeat3min"):
            interval = 180
        elif text.startswith("/repeat5min"):
            interval = 300
        else:
            send_message(chat_id, "Invalid repeat command.")
            return "OK"

        # Album case
        if "media_group_id" in replied_msg:
            mgid = replied_msg["media_group_id"]
            album = media_groups.get((chat_id, mgid), [])
            if album:
                job_ref = {"running": True}
                repeat_jobs.setdefault(chat_id, []).append(job_ref)
                threading.Thread(target=repeater, args=(chat_id, album, interval, job_ref, True), daemon=True).start()
                send_message(chat_id, f"✅ Started repeating album every {interval // 60} min.")
        else:
            # Single message repeat
            content = {}
            if "text" in replied_msg:
                content = {"text": replied_msg["text"]}
            elif "photo" in replied_msg:
                content = {"photo": replied_msg["photo"][-1]["file_id"], "caption": replied_msg.get("caption", "")}
            elif "video" in replied_msg:
                content = {"video": replied_msg["video"]["file_id"], "caption": replied_msg.get("caption", "")}
            else:
                send_message(chat_id, "❌ Unsupported message type for repeat.")
                return "OK"

            job_ref = {"running": True}
            repeat_jobs.setdefault(chat_id, []).append(job_ref)
            threading.Thread(target=repeater, args=(chat_id, content, interval, job_ref, False), daemon=True).start()
            send_message(chat_id, f"✅ Started repeating every {interval // 60} min.")

    elif text.startswith("/stop"):
        if chat_id in repeat_jobs:
            for job in repeat_jobs[chat_id]:
                job["running"] = False
            repeat_jobs[chat_id] = []
            send_message(chat_id, "🛑 Stopped all repeating messages.")

    return "OK"


@app.route("/")
def index():
    return "Bot is running!"


# -------------------- Keep Alive -------------------- #
def keep_alive():
    while True:
        try:
            requests.get(WEBHOOK_URL)
            print("✅ Keep-alive ping sent.")
        except Exception as e:
            print(f"❌ Keep-alive failed: {e}")
        time.sleep(300)


if __name__ == "__main__":
    requests.get(f"{BOT_API}/setWebhook?url={WEBHOOK_URL}/webhook")
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
