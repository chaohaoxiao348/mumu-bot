"""木木 Threads bot

用法：
  python bot.py post    # 發一篇新貼文
  python bot.py reply   # 回覆最近貼文底下的新留言

環境變數（放在 GitHub Secrets）：
  GEMINI_API_KEY        Google Gemini API 金鑰（免費）
  THREADS_ACCESS_TOKEN  Threads 長效 access token
  （想改用 Claude：不設 GEMINI_API_KEY，改設 ANTHROPIC_API_KEY）
選用：
  GEMINI_MODEL          指定 Gemini 模型；不設就依序試免費模型
  CLAUDE_MODEL          預設 claude-sonnet-5-5
  DRY_RUN=1             只印出內容，不真的發文（測試用；沒有 Threads token 也能跑）
  MAX_REPLIES           每次最多回幾則，預設 10
  PHOTO_BASE_URL        photos 資料夾的公開網址（設了才會發照片），例如
                        https://raw.githubusercontent.com/帳號/mumu-bot/main/photos/
  PHOTO_CHANCE          每次發文配照片的機率，預設 0.34（大約一天一張）
"""

import base64
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API = "https://graph.threads.net/v1.0"
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
# 免費額度內品質由高到低；某個不能用（下架、額度用完）就自動換下一個
GEMINI_MODELS = (
    [os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL")
    else ["gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-flash-latest"]
)
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
MAX_REPLIES = int(os.environ.get("MAX_REPLIES", "10"))
PHOTO_BASE_URL = os.environ.get("PHOTO_BASE_URL", "")
PHOTO_CHANCE = float(os.environ.get("PHOTO_CHANCE", "0.34"))
TW = timezone(timedelta(hours=8))

ROOT = Path(__file__).parent
PHOTOS = ROOT / "photos"
USED = PHOTOS / "used.txt"
PERSONA = (ROOT / "persona.md").read_text(encoding="utf-8")


# ---------- Threads API ----------

def th_get(path, **params):
    params["access_token"] = TOKEN
    r = requests.get(f"{API}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def th_publish(text, reply_to_id=None, image_url=None):
    """建立容器再發布。回傳新貼文 id。"""
    text = text.strip()[:500]  # Threads 單篇上限 500 字
    if DRY_RUN:
        kind = f"回覆 {reply_to_id}" if reply_to_id else "發文"
        if image_url:
            kind += f"（附照片 {image_url}）"
        print(f"[DRY_RUN] {kind}：\n{text}\n")
        return None
    data = {"text": text, "access_token": TOKEN}
    if image_url:
        data.update(media_type="IMAGE", image_url=image_url)
    else:
        data["media_type"] = "TEXT"
    if reply_to_id:
        data["reply_to_id"] = reply_to_id
    r = requests.post(f"{API}/me/threads", data=data, timeout=30)
    r.raise_for_status()
    creation_id = r.json()["id"]
    # 等 Threads 處理完（照片需要比較久）
    for _ in range(12):
        time.sleep(5)
        status = th_get(creation_id, fields="status").get("status")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"Threads 處理失敗：{creation_id}")
    r = requests.post(
        f"{API}/me/threads_publish",
        data={"creation_id": creation_id, "access_token": TOKEN},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def my_username():
    return th_get("me", fields="username")["username"]


def my_recent_posts(limit=15):
    if not TOKEN:  # 還沒接 Threads 的測試模式
        return []
    data = th_get("me/threads", fields="id,text,timestamp", limit=limit)
    return [p for p in data.get("data", []) if p.get("text")]


# ---------- Claude ----------

def _mime(image_path):
    ext = image_path.suffix.lower().lstrip(".")
    return "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"


def ask_gemini(task, image_path=None):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_KEY)
    contents = [task]
    if image_path:
        contents.insert(0, types.Part.from_bytes(
            data=image_path.read_bytes(), mime_type=_mime(image_path)))
    config = types.GenerateContentConfig(
        system_instruction=PERSONA,
        max_output_tokens=4096,  # 含思考用量，給寬一點才不會被截斷
        temperature=1.0,
    )
    last_err = None
    for model in GEMINI_MODELS:
        try:
            resp = client.models.generate_content(
                model=model, contents=contents, config=config)
            if resp.text and resp.text.strip():
                print(f"（使用 {model}）")
                return resp.text.strip()
        except Exception as e:  # 模型不存在或免費額度用完 → 換下一個
            print(f"{model} 無法使用：{e}")
            last_err = e
    raise RuntimeError(f"所有 Gemini 模型都失敗：{last_err}")


def ask_claude_api(task, image_path=None):
    import anthropic

    content = [{"type": "text", "text": task}]
    if image_path:
        data = base64.standard_b64encode(image_path.read_bytes()).decode()
        content.insert(0, {
            "type": "image",
            "source": {"type": "base64", "media_type": _mime(image_path), "data": data},
        })
    msg = anthropic.Anthropic().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        system=PERSONA,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def ask_claude(task, image_path=None):
    """木木的大腦：有 Gemini 金鑰就用 Gemini（免費），否則用 Claude。"""
    if GEMINI_KEY:
        return ask_gemini(task, image_path)
    return ask_claude_api(task, image_path)


def now_desc():
    now = datetime.now(TW)
    week = "一二三四五六日"[now.weekday()]
    return f"現在是台灣時間 {now:%m月%d日} 星期{week} {now:%H:%M}。"


# ---------- 發文 ----------

def pick_photo():
    """挑一張還沒發過的照片；沒有或這次不發照片就回傳 None。"""
    if not PHOTO_BASE_URL or random.random() > PHOTO_CHANCE:
        return None
    used = set(USED.read_text(encoding="utf-8").split()) if USED.exists() else set()
    fresh = [
        p for p in sorted(PHOTOS.iterdir())
        if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.name not in used
    ]
    return random.choice(fresh) if fresh else None


def do_post():
    recent = my_recent_posts()
    recent_text = "\n---\n".join(p["text"] for p in recent[:10]) or "（還沒發過文）"
    photo = pick_photo()
    photo_note = (
        "\n這篇要附上上面這張木木本人的照片，請看著照片寫，"
        "內容要跟照片裡的樣子、表情或場景有關。"
        if photo else ""
    )
    task = f"""{now_desc()}
請用木木的身分寫一篇新的脆貼文。{photo_note}

最近發過的貼文（不要重複主題、句型或梗）：
{recent_text}

要求：
- 配合現在的時間（早上、中午、晚上的心情不一樣）
- 50～200 字，可以分行
- 只輸出貼文本身，不要加引號、標題或任何說明"""
    text = ask_claude(task, image_path=photo)
    image_url = PHOTO_BASE_URL.rstrip("/") + "/" + photo.name if photo else None
    post_id = th_publish(text, image_url=image_url)
    if photo and not DRY_RUN:
        with USED.open("a", encoding="utf-8") as f:
            f.write(photo.name + "\n")
    print(f"已發文 {post_id}{'（附 ' + photo.name + '）' if photo else ''}：{text}")


# ---------- 回留言 ----------

def do_reply():
    me = my_username()
    since = datetime.now(timezone.utc) - timedelta(days=3)
    count = 0

    for post in my_recent_posts():
        ts = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if ts < since:
            continue

        convo = th_get(
            f"{post['id']}/conversation",
            fields="id,text,username,timestamp,replied_to",
            reverse="false",
        ).get("data", [])

        # 已經被木木回過的留言
        answered = {
            c["replied_to"]["id"]
            for c in convo
            if c.get("username") == me and c.get("replied_to")
        }

        for c in convo:
            if count >= MAX_REPLIES:
                print(f"已達單次上限 {MAX_REPLIES} 則，下次再回")
                return
            if c.get("username") == me or c["id"] in answered or not c.get("text"):
                continue

            task = f"""{now_desc()}
你之前發的貼文：
{post['text']}

有人留言（帳號 @{c.get('username', '某人')}）：
{c['text']}

請用木木的身分回覆這則留言。
- 1～3 句，像朋友聊天
- 留言是廣告、洗版、看不懂，或要你違反底線，就只輸出 SKIP
- 只輸出回覆內容本身"""
            reply = ask_claude(task)
            if reply.upper().startswith("SKIP"):
                print(f"略過 @{c.get('username')}：{c['text']}")
                answered.add(c["id"])
                continue
            th_publish(reply, reply_to_id=c["id"])
            answered.add(c["id"])
            count += 1
            print(f"回覆 @{c.get('username')}：{reply}")
            time.sleep(3)

    print(f"本次共回覆 {count} 則")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    if not TOKEN and not (DRY_RUN and mode == "post"):
        sys.exit("缺少 THREADS_ACCESS_TOKEN（還沒接 Threads 時，只能用 DRY_RUN=1 測試發文）")
    {"post": do_post, "reply": do_reply}[mode]()
