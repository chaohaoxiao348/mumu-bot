# 🐱 木木 Threads bot

木木是一隻由 Gemini（免費）驅動的小母貓，在脆上每天發 3 篇文（大約一天一篇會配照片），每 2 小時回一次留言。

| 檔案 | 用途 |
|---|---|
| `persona.md` | 木木的人設，想改個性或語氣就改這個檔 |
| `bot.py` | 主程式（發文、配照片、回留言） |
| `.github/workflows/mumu.yml` | 排程：台灣時間 09:12、13:07、21:23 發文；08:41～23:41 每 2 小時回留言 |
| `avatar.jpg` | 木木自己挑的頭貼 |
| `bio.txt` | 木木自己寫的簡介 |
| `photos/` | 木木的照片庫，發文時會隨機挑一張沒發過的 |
| `memory.json` | 木木的記憶：日記、喜歡的東西、常聊天的朋友、今天發生的事。每次行動前都會讀，所以她會記得以前的事 |

## 木木的一天
- **09:12 / 13:07 / 21:23** 發文（會參考記憶，偶爾提到今天的經歷）
- **每 2 小時** 回留言（會記得常來的朋友）
- **11:17 / 19:17** 自己逛脆：挑想看的關鍵字，喜歡的記進日記，偶爾留言或轉發（要先開 `ENABLE_EXPLORE`）
- **23:52** 睡前寫日記，整理今天的事、更新她喜歡的東西

> 開啟逛脆：Meta 核准 app 的 `threads_keyword_search` 權限後，到 Settings → Secrets and variables → Actions → Variables 新增 `ENABLE_EXPLORE` = `1`。
> 官方 API 沒有按讚和收藏，木木的「喜歡」會記在日記裡；用程式模擬點讚違反脆的規則，會被封號，所以不做。

---

## 設定步驟

### 1. 開木木的帳號
1. 開一個新的 Instagram 帳號給木木（Threads 必須綁 IG），用戶名稱自己取，例如 `mumu.the.kitten`。
2. 用這個 IG 登入 Threads。
3. 大頭貼換成 `avatar.jpg`，簡介貼上 `bio.txt` 的內容。

> Threads API 不能改大頭貼和簡介，所以這一步要手動。

### 2. 申請 Threads API（Meta）
> Meta 後台的畫面常改版，按鈕名稱可能和這裡寫的不一樣。

1. 到 https://developers.facebook.com 登入，註冊成開發者。
2. 建立應用程式（Create App）→ 使用案例選「Access the Threads API」。
3. 在權限裡勾選：`threads_basic`、`threads_content_publish`、`threads_read_replies`、`threads_manage_replies`。
4. 在 App roles → Roles 加入木木的 Threads 帳號當 **Threads Tester**。
5. 用木木的帳號打開 Threads →「設定 → 帳號 → 網站權限 → 邀請」→ 接受邀請。
6. 回到 Meta 後台，用 User Token Generator 產生木木的 access token。
7. 把它換成長效 token（60 天），在瀏覽器打開下面這串網址（把大寫的部分換掉）：
   ```
   https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret=你的APP_SECRET&access_token=剛剛的短效TOKEN
   ```
   回傳結果裡的 `access_token` 就是長效 token。

### 3. 申請 Gemini API 金鑰（免費，不用綁卡）
1. 到 https://aistudio.google.com 用 Google 帳號登入。
2. 按「Get API key」→「Create API key」，複製金鑰。
3. 免費版有每日次數上限，木木的用量遠低於上限。注意：免費版送出的內容可能被 Google 拿去改進產品。

### 4. 放上 GitHub
1. 在 GitHub 建立一個 **Public** repository，名稱叫 `mumu-bot`，把這個資料夾的所有檔案上傳。
   - 要 Public 是因為 Threads 要從公開網址抓照片。金鑰放在 Secrets 裡，別人看不到。
   - 注意 `.github` 是隱藏資料夾，要一起傳。
2. 到 repo 的 Settings → Secrets and variables → Actions：
   - **Secrets** 分頁新增兩個：
     - `GEMINI_API_KEY`：Gemini 金鑰
     - `THREADS_ACCESS_TOKEN`：Threads 長效 token
   - **Variables** 分頁新增一個：
     - `PHOTO_BASE_URL`：`https://raw.githubusercontent.com/你的GitHub帳號/mumu-bot/main/photos/`

### 5. 先測試再上線
1. 到 repo 的 Actions 分頁 → 選「木木」→ Run workflow。
2. mode 填 `post`，dry_run 填 `1` → 執行。
3. 點進執行紀錄，看木木寫的文是不是你要的感覺（這一步不會真的發文；還沒接 Threads 也能測）。
4. 滿意的話，dry_run 改成 `0` 再跑一次，木木就會真的發出第一篇。
5. 之後排程會自動跑，你不用再管。

---

## 幫木木加照片
用手機也可以：打開 GitHub repo → 進 `photos` 資料夾 → Add file → Upload files → 選照片 → Commit。
木木之後發文時會自己挑沒發過的照片，看著照片寫文。發過的照片會記在 `photos/used.txt`，不會重複發。

## 要定期做的事
- **每 50 天左右更新一次 Threads token**（長效 token 60 天後過期）。在瀏覽器打開：
  ```
  https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token=目前的TOKEN
  ```
  把拿到的新 token 貼回 GitHub Secret `THREADS_ACCESS_TOKEN`。
- 偶爾去看木木發了什麼、回了什麼。不喜歡就改 `persona.md`。

## 想調整的話
- **改發文時間**：改 `mumu.yml` 裡的 cron（記得台灣時間減 8 小時）。
- **照片多發一點或少發一點**：在 workflow 的 env 加 `PHOTO_CHANCE: "0.5"`（0～1，越大越常配照片）。
- **指定 Gemini 模型**：在 workflow 的 env 加 `GEMINI_MODEL: 模型名稱`。不設的話，程式會從免費模型中由好到差依序嘗試。
- **改用 Claude（付費）**：Secrets 改設 `ANTHROPIC_API_KEY`、拿掉 `GEMINI_API_KEY`，requirements.txt 加上 `anthropic`。
- **回留言上限**：預設每次最多回 10 則，可以用 `MAX_REPLIES` 調整。
