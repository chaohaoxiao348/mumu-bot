"""木木 Threads bot

用法：
  python bot.py post     # 發一篇新貼文
  python bot.py reply    # 回覆最近貼文底下的新留言
  python bot.py explore  # 自己去逛脆：搜喜歡的主題、留言、轉發（要開 ENABLE_EXPLORE）
  python bot.py diary    # 睡前寫日記，整理今天的經歷、更新喜好

木木的記憶存在 memory.json（日記、喜歡的東西、朋友、今天發生的事），
每次行動前都會先讀，所以她會記得以前的事。

環境變數（放在 GitHub Secrets / Variables）：
  GEMINI_API_KEY        Google Gemini API 金鑰（免費）
  THREADS_ACCESS_TOKEN  Threads 長效 access token
  （想改用 Claude：不設 GEMINI_API_KEY，改設 ANTHROPIC_API_KEY）
選用：
  ENABLE_EXPLORE=1      開啟逛脆功能（Meta 核准 threads_keyword_search 之後再開）
  GEMINI_MODEL          指定 Gemini 模型；不設就依序試免費模型
  CLAUDE_MODEL          預設 claude-sonnet-5-5
  DRY_RUN=1             只印出內容，不真的發文、也不改記憶（測試用）
  MAX_REPLIES           每次最多回幾則留言，預設 10
  PHOTO_BASE_URL        photos 資料夾的公開網址（設了才會發照片）
  PHOTO_CHANCE          每次發文配照片的機率，預設 0.34（大約一天一張）
"""

import base64
import json
import os
import random
import re
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
ENABLE_EXPLORE = os.environ.get("ENABLE_EXPLORE") == "1"
MAX_REPLIES = int(os.environ.get("MAX_REPLIES", "10"))
PHOTO_BASE_URL = os.environ.get("PHOTO_BASE_URL", "")
PHOTO_CHANCE = float(os.environ.get("PHOTO_CHANCE", "0.34"))
TW = timezone(timedelta(hours=8))

ROOT = Path(__file__).parent
PHOTOS = ROOT / "photos"
USED = PHOTOS / "used.txt"
MEMORY_FILE = ROOT / "memory.json"
PERSONA = (ROOT / "persona.md").read_text(encoding="utf-8")


# ---------- 記憶 ----------

def load_memory():
    mem = {}
    if MEMORY_FILE.exists():
        mem = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    mem.setdefault("interests", [])
    mem.setdefault("friends", {})
    mem.setdefault("liked_posts", [])
    mem.setdefault("events", [])
    mem.setdefault("diary", [])
    mem.setdefault("seen_ids", [])
    return mem


def save_memory(mem):
    if DRY_RUN:
        print("[DRY_RUN] 記憶不存檔")
        return
    # 只留最近的，避免越來越大
    mem["diary"] = mem["diary"][-30:]
    mem["liked_posts"] = mem["liked_posts"][-40:]
    mem["events"] = mem["events"][-80:]
    mem["seen_ids"] = mem["seen_ids"][-300:]
    mem["interests"] = mem["interests"][:15]
    top = sorted(mem["friends"].items(), key=lambda kv: -kv[1].get("count", 0))[:40]
    mem["friends"] = dict(top)
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")


def log_event(mem, kind, text):
    mem["events"].append({"time": datetime.now(TW).strftime("%m/%d %H:%M"),
                          "type": kind, "text": text[:200]})


def memory_context(mem):
    """把記憶整理成一段文字，放進每次的提示裡。"""
    parts = []
    if mem["interests"]:
        parts.append("木木最近喜歡的東西：" + "、".join(mem["interests"]))
    if mem["diary"]:
        recent = mem["diary"][-3:]
        parts.append("木木最近的日記：\n" + "\n".join(f"［{d['date']}］{d['text']}" for d in recent))
    if mem["friends"]:
        top = sorted(mem["friends"].items(), key=lambda kv: -kv[1].get("count", 0))[:8]
        parts.append("常互動的脆友：\n" + "\n".join(
            f"@{u}（聊過 {f.get('count', 0)} 次）{f.get('note', '')}" for u, f in top))
    if mem["liked_posts"]:
        parts.append("最近在脆上看到、喜歡的貼文：\n" + "\n".join(
            f"@{p['username']}：{p['text'][:60]}（{p.get('why', '')}）" for p in mem["liked_posts"][-5:]))
    if mem["events"]:
        parts.append("今天發生的事：\n" + "\n".join(
            f"{e['time']} {e['text']}" for e in mem["events"][-10:]))
    if not parts:
        return "（木木的記憶還是空的，這是她剛開始在脆上活動的時候。）"
    return "\n\n".join(parts)


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


def th_repost(media_id):
    if DRY_RUN:
        print(f"[DRY_RUN] 轉發 {media_id}")
        return
    r = requests.post(f"{API}/{media_id}/repost", data={"access_token": TOKEN}, timeout=30)
    r.raise_for_status()


def my_username():
    return th_get("me", fields="username")["username"]


def my_recent_posts(limit=15):
    if not TOKEN:  # 還沒接 Threads 的測試模式
        return []
    data = th_get("me/threads", fields="id,text,timestamp", limit=limit)
    return [p for p in data.get("data", []) if p.get("text")]


# ---------- 大腦（Gemini / Claude） ----------

def _mime(image_path):
    ext = image_path.suffix.lower().lstrip(".")
    return "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"


def ask_gemini(task, image_path=None, want_json=False):
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
        response_mime_type="application/json" if want_json else None,
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


def ask_claude_api(task, image_path=None, want_json=False):
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
        max_tokens=1200,
        system=PERSONA,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def think(task, image_path=None):
    """木木的大腦：有 Gemini 金鑰就用 Gemini（免費），否則用 Claude。"""
    if GEMINI_KEY:
        return ask_gemini(task, image_path)
    return ask_claude_api(task, image_path)


def think_json(task):
    raw = ask_gemini(task, want_json=True) if GEMINI_KEY else ask_claude_api(task)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    return json.loads(raw)


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


def do_post(mem):
    recent = my_recent_posts()
    recent_text = "\n---\n".join(p["text"] for p in recent[:10]) or "（還沒發過文）"
    photo = pick_photo()
    photo_note = (
        "\n這篇要附上上面這張木木本人的照片，請看著照片寫，"
        "內容要跟照片裡的樣子、表情或場景有關。"
        if photo else ""
    )
    task = f"""{now_desc()}
【木木的記憶】
{memory_context(mem)}

請用木木的身分寫一篇新的脆貼文。{photo_note}
可以自然地提到記憶裡的事（例如今天在脆上看到的貼文、常來聊天的朋友、日記裡的心情），
但不要每篇都提，也不要硬塞。

最近發過的貼文（不要重複主題、句型或梗）：
{recent_text}

要求：
- 配合現在的時間（早上、中午、晚上的心情不一樣）
- 50～200 字，可以分行
- 只輸出貼文本身，不要加引號、標題或任何說明"""
    text = think(task, image_path=photo)
    image_url = PHOTO_BASE_URL.rstrip("/") + "/" + photo.name if photo else None
    post_id = th_publish(text, image_url=image_url)
    if photo and not DRY_RUN:
        with USED.open("a", encoding="utf-8") as f:
            f.write(photo.name + "\n")
    log_event(mem, "post", f"發了一篇文：{text}")
    print(f"已發文 {post_id}{'（附 ' + photo.name + '）' if photo else ''}：{text}")


# ---------- 回留言 ----------

def do_reply(mem):
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

            user = c.get("username", "某人")
            friend = mem["friends"].get(user, {})
            friend_note = (
                f"你認識 @{user}，你們聊過 {friend.get('count', 0)} 次。{friend.get('note', '')}"
                if friend else f"@{user} 是第一次來留言的新朋友。"
            )
            task = f"""{now_desc()}
【木木的記憶】
{memory_context(mem)}

你之前發的貼文：
{post['text']}

有人留言（帳號 @{user}）：
{c['text']}

{friend_note}

請用木木的身分回覆這則留言。
- 1～3 句，像朋友聊天；認識的朋友可以更熟一點
- 留言是廣告、洗版、看不懂，或要你違反底線，就只輸出 SKIP
- 只輸出回覆內容本身"""
            reply = think(task)
            answered.add(c["id"])
            if reply.upper().startswith("SKIP"):
                print(f"略過 @{user}：{c['text']}")
                continue
            th_publish(reply, reply_to_id=c["id"])
            count += 1
            f = mem["friends"].setdefault(user, {"count": 0, "note": ""})
            f["count"] = f.get("count", 0) + 1
            f["last"] = datetime.now(TW).strftime("%Y-%m-%d")
            log_event(mem, "reply", f"@{user} 留言「{c['text'][:40]}」，我回「{reply[:40]}」")
            print(f"回覆 @{user}：{reply}")
            time.sleep(3)

    print(f"本次共回覆 {count} 則")


# ---------- 自己逛脆 ----------

def do_explore(mem):
    if not ENABLE_EXPLORE:
        print("逛脆功能還沒開（等 Meta 核准 threads_keyword_search 後，把 ENABLE_EXPLORE 設成 1）")
        return
    me = my_username()

    pick = think_json(f"""{now_desc()}
【木木的記憶】
{memory_context(mem)}

木木現在想去脆上逛逛。她會想搜尋什麼？
選一個貓咪會好奇、跟她喜好或最近心情有關的關鍵字（2～6 個字，繁體中文）。
輸出 JSON：{{"keyword": "關鍵字", "mood": "為什麼想看這個（一句話）"}}""")
    keyword = pick["keyword"]
    print(f"木木想看：{keyword}（{pick.get('mood', '')}）")

    found = th_get("keyword_search", q=keyword, search_type="TOP",
                   fields="id,text,username,timestamp,permalink", limit=15).get("data", [])
    seen = set(mem["seen_ids"])
    posts = [p for p in found
             if p.get("text") and p.get("username") != me and p["id"] not in seen][:10]
    if not posts:
        log_event(mem, "explore", f"去搜了「{keyword}」，但沒看到什麼新東西")
        print("沒有新的貼文")
        return

    listing = "\n".join(f"[{i}] @{p['username']}：{p['text'][:150]}" for i, p in enumerate(posts))
    decision = think_json(f"""{now_desc()}
【木木的記憶】
{memory_context(mem)}

木木搜尋「{keyword}」，看到這些貼文：
{listing}

她會怎麼反應？每篇可以選：
- "like"：心裡喜歡，記在日記（不會留下公開痕跡）
- "reply"：留言（要附上留言內容，1～2 句，木木的語氣，自然不推銷）
- "repost"：喜歡到想轉發
- "skip"：沒興趣

規則：
- 最多 2 則 reply、1 則 repost；不必每篇都互動，大部分 skip 是正常的
- 只跟貓、寵物、可愛、日常、療癒類的貼文互動
- 政治、爭議、悲傷、求助、廣告、看起來是小孩的帳號 → 一律 skip
- 不要在留言裡打廣告或叫人追蹤

輸出 JSON：{{"actions": [{{"index": 0, "action": "like", "reply": "", "why": "一句話"}}]}}""")

    replies = reposts = 0
    for a in decision.get("actions", []):
        try:
            p = posts[int(a["index"])]
        except (KeyError, ValueError, IndexError):
            continue
        act = a.get("action", "skip")
        why = a.get("why", "")
        mem["seen_ids"].append(p["id"])
        if act == "skip":
            continue
        if act == "reply" and a.get("reply") and replies < 2:
            th_publish(a["reply"], reply_to_id=p["id"])
            replies += 1
            log_event(mem, "explore", f"在 @{p['username']} 的貼文「{p['text'][:40]}」留言：{a['reply'][:40]}")
            print(f"留言 @{p['username']}：{a['reply']}")
        elif act == "repost" and reposts < 1:
            th_repost(p["id"])
            reposts += 1
            log_event(mem, "explore", f"轉發了 @{p['username']} 的「{p['text'][:40]}」")
            print(f"轉發 @{p['username']}")
        mem["liked_posts"].append({
            "date": datetime.now(TW).strftime("%Y-%m-%d"),
            "username": p["username"], "text": p["text"][:200], "why": why,
        })
        if act == "like":
            log_event(mem, "explore", f"看到 @{p['username']} 的「{p['text'][:40]}」，很喜歡（{why}）")
            print(f"喜歡 @{p['username']}：{why}")
        time.sleep(3)


# ---------- 睡前日記 ----------

def do_diary(mem):
    today = datetime.now(TW).strftime("%Y-%m-%d")
    result = think_json(f"""{now_desc()}
【木木的記憶】
{memory_context(mem)}

木木要睡覺了，請用她的口吻寫今天的日記，並整理她的喜好。
- 日記 60～150 字，第一人稱，寫今天在脆上的經歷和心情；今天沒什麼事就寫在家的小事
- interests：她現在喜歡的東西清單（最多 12 個，保留舊的、加入今天新喜歡的，膩了的可以拿掉）
- friend_notes：對今天有互動的脆友的一句印象（沒有就給空物件）

輸出 JSON：{{"diary": "...", "interests": ["..."], "friend_notes": {{"帳號": "一句印象"}}}}""")
    mem["diary"].append({"date": today, "text": result["diary"]})
    if result.get("interests"):
        mem["interests"] = result["interests"]
    for user, note in (result.get("friend_notes") or {}).items():
        user = user.lstrip("@")
        if user in mem["friends"]:
            mem["friends"][user]["note"] = note
    mem["events"] = []  # 今天的事寫進日記了，清空
    print(f"［{today} 的日記］\n{result['diary']}\n\n現在喜歡：{'、'.join(mem['interests'])}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    offline_ok = DRY_RUN and mode in ("post", "diary")
    if not TOKEN and not offline_ok:
        sys.exit("缺少 THREADS_ACCESS_TOKEN（還沒接 Threads 時，只能用 DRY_RUN=1 測試 post 和 diary）")
    memory = load_memory()
    {"post": do_post, "reply": do_reply, "explore": do_explore, "diary": do_diary}[mode](memory)
    save_memory(memory)
