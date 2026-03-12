# Lingdong Price Website（由 kinyo-price 複製）

此專案由 `D:\SAM-KINYO-WEBSITE\kinyo-price` 複製而來，並已切換為：
- 靈動數碼品牌模式
- Firebase 正式模式（Email/Password 登入 + Firestore）
- 商品可由固定格式 Excel 直接匯入 Firestore（分流唯一鍵）

## 技術棧

- 前端：HTML + 原生 JavaScript (ES Modules)、React 18 (CDN)、Tailwind CSS
- 後端：Python FastAPI、Uvicorn
- 資料庫：Firebase Firestore
- 認證：Firebase Authentication
- 外部整合：LINE Messaging API、Google Apps Script
- 部署：GitHub Pages（前端）、GCP Cloud Run（後端）

## 專案結構

```text
kinyo-price/
├── index.html                  # 首頁
├── system.html                 # 查價系統
├── system-v2/system.html       # 舊版入口（轉址到 system.html）
├── order.html                  # 訂單系統（React CDN）
├── tracking.html               # 追蹤系統（React CDN）
├── return_form.html            # 售後表單（React CDN）
├── js/
│   ├── app.js                  # 查價前端入口
│   ├── auth.js                 # 登入/登出
│   ├── search.js               # 搜尋邏輯
│   ├── render.js               # 結果渲染
│   ├── quote.js                # 報價單
│   ├── export.js               # Excel/PPT 匯出
│   ├── import.js               # 商品匯入
│   ├── state.js                # 共用狀態
│   └── firebase-init.js        # Firebase 初始化
├── backend/
│   ├── main.py                 # FastAPI 入口
│   ├── core/config.py          # 環境變數與 Firebase 初始化
│   ├── database/firestore_db.py# 商品快取層
│   ├── routers/
│   │   ├── webhook_api.py      # LINE Webhook
│   │   └── system_api.py       # 快取刷新 API
│   ├── services/pricing_service.py
│   └── utils/
└── modelData.json              # 型號與圖片對照
```

## 核心流程

### 1) Web 查價流程

1. 使用者登入 Firebase Auth
2. 前端載入 Firestore 商品到 `state.productCache`
3. 執行搜尋/排序/加入報價
4. 匯出 Excel/PPT 或歷史報價

### 2) LINE Bot 查價流程

1. LINE 事件進入 `POST /api/webhook`
2. 解析文字（數量、預算、關鍵字）
3. 以快取搜尋商品（必要時模糊比對）
4. 計算階梯報價
5. 回傳 Flex Message（單卡或 Carousel）

### 3) 後端快取流程

1. FastAPI 啟動時載入 Firestore `Products`
2. 快取保存於記憶體，查價以快取為主
3. 管理者可呼叫 `POST /api/refresh?token=...` 重新載入

## API 一覽（後端）

- `GET /health`：健康檢查與快取數量
- `POST /api/webhook`：LINE Webhook
- `POST /api/refresh?token=...`：手動刷新快取
- `GET /api/cache-stats`：快取統計

## 本機開啟（重點）

1. 進入專案：
```powershell
cd D:\LINGDONG_PROJECT\lingdong-price
```

2. 啟動靜態伺服器（擇一）：
```powershell
python -m http.server 5600
```

3. 開啟：
- `http://localhost:5600/system.html`
- 或 `http://localhost:5600/brands/lingdong/system.html`

## 已部署環境
- GitHub Repo: `https://github.com/Sam-Kinyo/Lingdong-price`
- Firebase Project: `lingdong-price`
- Hosting URL: `https://lingdong-price.web.app`

## Excel 資料來源
- 來源檔（上傳用）：`c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx`
- 匯入入口：系統頁 `📥 匯入產品總表 (同步上下架)` 按鈕
- 寫入目標：Firestore `Products` 集合（文件 ID = `splitCode`）

## 商品圖片爬取工具（Python）
- 工具檔：`tools/fetch_product_images.py`
- 作用：讀取 Excel 的 `型號` + `商品對應網站`，自動爬網址找商品圖並下載到本機。

### 安裝套件
```powershell
pip install requests beautifulsoup4 pandas openpyxl
```

### 執行範例
```powershell
python tools/fetch_product_images.py --input "c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx"
```
- 執行時會顯示即時進度，例如：`[15/103] ok model=...`
- 如需安靜模式可加：`--no-progress`

### 進階模式（新增）
- 只抓新增（預設開啟，已下載會略過）：
```powershell
python tools/fetch_product_images.py --input "c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx" --only-new
```
- 多執行緒加速（例如 6 線程）：
```powershell
python tools/fetch_product_images.py --input "c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx" --workers 6
```
- 網站容易擋流量時，放慢請求並增加重試：
```powershell
python tools/fetch_product_images.py --input "c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx" --workers 3 --min-host-interval 0.8 --retries 5 --retry-delay 2
```

### 直接同步到 system（不存本機）
> 需要先準備 Firebase service account JSON（例如 `D:\keys\lingdong-price-admin.json`）

```powershell
python tools/fetch_product_images.py --input "c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx" --workers 3 --min-host-interval 0.8 --no-save-local --upload-to-storage --update-firestore --firebase-cred "D:\LINGDONG_PROJECT\lingdong-price\backend\serviceAccountKey.json" --firebase-bucket "lingdong-price.firebasestorage.app"
```

- 作用：先上傳圖片到 Firebase Storage，再更新 Firestore `Products/{splitCode}.imageUrl`
- 前台 `system.html` 會優先顯示 `imageUrl`，所以會立即對應到商品圖片

### 雙擊啟動（免記指令）
- 入口檔：`run_image_sync.bat`（直接點兩下即可）
- 內部會呼叫：`tools/run_image_sync.ps1`
- 預設模式：`full-refresh`（等同 `--no-only-new`，會全部重新同步）
- 若要改成只抓新增，打開 `tools/run_image_sync.ps1`，把 `$OnlyNew` 參數改成預設啟用即可。

### 輸出結果
- 圖片資料夾：`downloaded_images\`
- 報表：`download_report.csv`（含成功/略過/失敗、圖片網址、錯誤原因）

## 欄位固定規格（單一分頁）
- 固定欄位：`品牌`、`分類`、`分流`、`國際條碼`、`型號`、`商品名稱`、`詢價含`、`市價含`、`售價含`、`箱入數`、`BSMI`、`NCC`、`狀態`、`商品對應網站`
- 唯一鍵：`分流`（`splitCode`）。後續更新上下架、價格、欄位都以分流辨識。
- `分流` 重複時：以 Excel 最後一筆覆蓋（更新語意）。
- `分流` 空白時：該筆略過，不寫入 JSON。

## 狀態映射規則（已實作）
- `停產`、`下架` -> `inactive`（前台不顯示）
- `一般商品`、`缺貨中` -> `active`（前台顯示）
- 目前不做真實庫存，僅依狀態給概略值：
  - `缺貨中` -> `inventory = 0`
  - 其他可顯示狀態 -> `inventory = 200`（暫定值）

## Firebase Auth（Email/Password）
1. 至 Firebase Console -> Authentication -> Sign-in method 啟用 `Email/Password`
2. 新增使用者（Authentication -> Users）
3. 在 Firestore 建立 `Users/{docId}` 文件（`docId` 可用 email 小寫、原始 email 或 auth uid），欄位建議：
   - `level`：數字 0~4（決定可見報價層級）
   - `groupBuy`：`true/false`（選填）
   - `vipColumn`、`vipName`（VIP 客戶選填）
4. 等級對應：
   - `L1`：50 / 100
   - `L2`：50 / 100 / 300
   - `L3`：50 / 100 / 300 / 500 / 1000
   - `L4`：50 / 100 / 300 / 500 / 1000 / 3000 + 匯入產品按鈕
5. 目前 `system.html` 已是 Firebase 正式模式（`window.__USE_LOCAL_DB__ = false`）

## 正式模式首次上線建議流程
1. 先用管理員帳號登入 `https://lingdong-price.web.app/system.html`
2. 點 `📥 匯入產品總表 (同步上下架)`，上傳 `LingDong商品總表.xlsx`
3. 匯入完成會自動刷新，前台即改讀 Firestore 正式資料

## 已知架構特性

- 前端採混合模式（原生 JS 與 React CDN 並存）
- 前端直接連 Firestore（部分路徑未經後端 API）
- `system-v2/system.html` 僅保留相容轉址，實際邏輯統一於 `system.html`

## 建議優化方向

- 統一前端建置流程（例如 Vite）
- 將關鍵資料讀寫收斂到後端 API，強化權限控管
- 維持單一查價系統入口與共用程式碼，避免版本漂移
- 增加後端自動化測試（尤其 pricing 與 parser）
