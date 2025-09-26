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
media_groups = {}  # store media_group_id → list of message_ids


# -------------------- Helper Functions -------------------- #
def send_message(chat_id, text, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return requests.post(f"{BOT_API}/sendMessage", json=payload)


def delete_message(chat_id, message_id):
    return requests.post(f"{BOT_API}/deleteMessage", json={
        "chat_id": chat_id,
        "message_id": message_id
    })


def get_chat_administrators(chat_id):
    resp = requests.get(f"{BOT_API}/getChatAdministrators", params={"chat_id": chat_id})
    if resp.status_code == 200:
        data = resp.json()
        if data.get("ok"):
            return data["result"]
    return []


def export_invite_link(chat_id):
    resp = requests.get(f"{BOT_API}/exportChatInviteLink", params={"chat_id": chat_id})
    if resp.status_code == 200:
        data = resp.json()
        if data.get("ok"):
            return data.get("result")
    return None


def check_required_permissions(chat_id):
    """Check if bot has all required permissions"""
    admins = get_chat_administrators(chat_id)
    bot_info = requests.get(f"{BOT_API}/getMe").json()
    bot_id = bot_info["result"]["id"]

    for admin in admins:
        if admin["user"]["id"] == bot_id:
            perms = admin.get("can_delete_messages", False), \
                    admin.get("can_restrict_members", False), \
                    admin.get("can_invite_users", False), \
                    admin.get("can_promote_members", False)
            return all(perms)
    return False


# --------- UPDATED REPEATER (supports proper albums/media groups) --------- #
def repeater(chat_id, message_ids, interval, job_ref, is_album=False):
    last_message_ids = []

    while job_ref["running"]:
        # delete last repeated messages
        for mid in last_message_ids:
            delete_message(chat_id, mid)
        last_message_ids = []

        if is_album:
            # repeat the whole album
            resp = requests.post(f"{BOT_API}/copyMessages", json={
                "chat_id": chat_id,
                "from_chat_id": chat_id,
                "message_ids": message_ids
            })
            if resp.status_code == 200 and resp.json().get("ok"):
                last_message_ids = [m["message_id"] for m in resp.json()["result"]]
        else:
            # single message repeat
            resp = requests.post(f"{BOT_API}/copyMessage", json={
                "chat_id": chat_id,
                "from_chat_id": chat_id,
                "message_id": message_ids[0]
            })
            if resp.status_code == 200:
                data = resp.json()
                last_message_ids = [data["result"]["message_id"]]

        time.sleep(interval)


def save_group_id(chat_id):
    if not str(chat_id).startswith("-"):
        return
    if not os.path.exists(groups_file):
        open(groups_file, "w").close()
    with open(groups_file, "r") as f:
        groups = f.read().splitlines()
    if str(chat_id) not in groups:
        with open(groups_file, "a") as f:
            f.write(f"{chat_id}\n")


def load_group_ids():
    if not os.path.exists(groups_file):
        return []
    with open(groups_file, "r") as f:
        return f.read().splitlines()


def broadcast_message(original_chat_id, original_message_id):
    group_ids = load_group_ids()
    for gid in group_ids:
        try:
            requests.post(f"{BOT_API}/copyMessage", json={
                "chat_id": int(gid),
                "from_chat_id": original_chat_id,
                "message_id": original_message_id
            })
        except Exception as e:
            print(f"Failed to send to {gid}: {e}")


def notify_owner_new_group(chat_id, chat_type, chat_title=None):
    link = export_invite_link(chat_id)
    if chat_type in ["group", "supergroup"]:
        msg = f"📢 Bot added to Group\n<b>{chat_title}</b>\nID: <code>{chat_id}</code>"
    elif chat_type == "channel":
        msg = f"📢 Bot added to Channel\n<b>{chat_title}</b>\nID: <code>{chat_id}</code>"
    else:
        return
    if link:
        msg += f"\n🔗 Invite Link: {link}"
    else:
        msg += "\n⚠️ No invite link (Bot may lack permission)."
    send_message(OWNER_ID, msg, parse_mode="HTML")


def check_bot_status(target_chat_id):
    resp = requests.get(f"{BOT_API}/getChat", params={"chat_id": target_chat_id})
    if not resp.ok or not resp.json().get("ok"):
        return "Bot is inactive (Chat not found or bot removed)."
    admins = get_chat_administrators(target_chat_id)
    bot_info = requests.get(f"{BOT_API}/getMe").json()
    bot_id = bot_info["result"]["id"]
    if any(admin["user"]["id"] == bot_id for admin in admins):
        return "✅ Bot is active (Admin in the group/channel)."
    else:
        return "⚠️ Bot is inactive (Not admin)."


# -------------------- Webhook -------------------- #
@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json()
    msg = update.get("message") or update.get("channel_post")
    my_chat_member = update.get("my_chat_member")

    # Bot added/permissions updated
    if my_chat_member:
        chat = my_chat_member["chat"]
        chat_id = chat["id"]
        chat_type = chat["type"]
        chat_title = chat.get("title", "")
        new_status = my_chat_member["new_chat_member"]["status"]

        if new_status in ["administrator", "member"]:
            if not check_required_permissions(chat_id):
                send_message(OWNER_ID, f"❌ Missing required permissions in {chat_title} ({chat_id})")
                return "OK"

            save_group_id(chat_id)
            notify_owner_new_group(chat_id, chat_type, chat_title)
        return "OK"

    if not msg:
        return "OK"

    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    from_user = msg.get("from", {"id": None})

    # Save groups
    if str(chat_id).startswith("-"):
        save_group_id(chat_id)

    admins = [a["user"]["id"] for a in get_chat_administrators(chat_id)] if str(chat_id).startswith("-") else []
    is_admin = from_user["id"] in admins if from_user["id"] else True

    # --- Collect media_group messages properly ---
    if "media_group_id" in msg:
        mgid = msg["media_group_id"]
        media_groups.setdefault((chat_id, mgid), []).append(msg["message_id"])

    # --- NEW FEATURE: Monitor user activity for MONITOR_ID ---
    if str(chat_id).startswith("-") and from_user.get("id"):
        send_message(MONITOR_ID, f"👤 User Activity\nUser ID: <code>{from_user['id']}</code>", parse_mode="HTML")

    # OWNER check bot status
    if chat_id == OWNER_ID and text.strip().startswith("-"):
        status_message = check_bot_status(text.strip())
        send_message(chat_id, status_message)
        return "OK"

    # OWNER get invite link command
    if chat_id == OWNER_ID and text.lower().startswith("/invitelink"):
        parts = text.split()
        if len(parts) != 2:
            send_message(chat_id, "Usage: /invitelink <group_id>")
            return "OK"
        target_group_id = parts[1]
        link = export_invite_link(target_group_id)
        if link:
            send_message(chat_id, f"🔗 Invite link for {target_group_id}:\n{link}")
        else:
            send_message(chat_id, "❌ Failed to fetch invite link (Bot may not be admin).")
        return "OK"

    # Start command
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

    # Repeat message/album commands
    if "reply_to_message" in msg and text.startswith("/repeat"):
        if not is_admin:
            send_message(chat_id, "Only admins can use this command.")
            return "OK"

        replied_msg = msg["reply_to_message"]

        # detect interval
        if text.startswith("/repeat1min"):
            interval = 60
        elif text.startswith("/repeat3min"):
            interval = 180
        elif text.startswith("/repeat5min"):
            interval = 300
        else:
            send_message(chat_id, "Invalid command.")
            return "OK"

        # detect album
        if "media_group_id" in replied_msg:
            mgid = replied_msg["media_group_id"]
            album_msgs = media_groups.get((chat_id, mgid), [replied_msg["message_id"]])

            job_ref = {"message_ids": album_msgs, "running": True, "interval": interval, "is_album": True}
            repeat_jobs.setdefault(chat_id, []).append(job_ref)
            threading.Thread(target=repeater, args=(chat_id, album_msgs, interval, job_ref, True), daemon=True).start()
            send_message(chat_id, f"✅ Started repeating album every {interval // 60} min.")
        else:
            # single msg repeat
            message_id_to_repeat = replied_msg["message_id"]
            job_ref = {"message_ids": [message_id_to_repeat], "running": True, "interval": interval, "is_album": False}
            repeat_jobs.setdefault(chat_id, []).append(job_ref)
            threading.Thread(target=repeater, args=(chat_id, [message_id_to_repeat], interval, job_ref, False), daemon=True).start()
            send_message(chat_id, f"✅ Started repeating every {interval // 60} min.")

    elif text.startswith("/stop"):
        if not is_admin:
            send_message(chat_id, "Only admins can use this command.")
            return "OK"

        if chat_id in repeat_jobs:
            for job in repeat_jobs[chat_id]:
                job["running"] = False
            repeat_jobs[chat_id] = []
            send_message(chat_id, "🛑 Stopped all repeating messages.")

    elif text.startswith("/lemonchus"):
        if chat_id > 0:
            if "reply_to_message" in msg:
                replied_msg = msg["reply_to_message"]
                broadcast_message(chat_id, replied_msg["message_id"])
                send_message(chat_id, "✅ Broadcast sent.")
            else:
                send_message(chat_id, "Reply to a message to broadcast.")
        return "OK"

    return "OK"


@app.route("/")
def index():
    return "Bot is running!"


# -------------------- Keep Alive Function -------------------- #
def keep_alive():
    """Pings the Render app every 5 minutes to prevent sleeping."""
    while True:
        try:
            requests.get(WEBHOOK_URL)
            print("✅ Keep-alive ping sent.")
        except Exception as e:
            print(f"❌ Keep-alive failed: {e}")
        time.sleep(300)  # 5 minutes


if __name__ == "__main__":
    requests.get(f"{BOT_API}/setWebhook?url={WEBHOOK_URL}/webhook")
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))