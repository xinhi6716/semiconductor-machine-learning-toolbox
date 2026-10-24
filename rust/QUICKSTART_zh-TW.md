# 半導體機器學習工具箱：原生 Rust 版

這是本機命令列工具 `smlt`，不是 Python 包裝器。執行不需要 Python、Rust、API 金鑰或網路。10 個核心工具的計算、CSV 驗證與 JSON 輸出皆由 Rust 執行。

## Windows 10/11 x64

1. 解壓縮 Windows ZIP 到自己的資料夾。
2. 在該資料夾開啟 PowerShell。
3. 執行：

```powershell
.\smlt.exe --help
.\smlt.exe iv-readpoint --help
.\smlt.exe iv-readpoint .\examples\iv-readpoint\synthetic_iv.csv --voltage-unit V --current-unit uA --read-voltage 100 --read-voltage-unit mV --output .\readpoint-report.json
$LASTEXITCODE
```

第三行應產生 `readpoint-report.json`，退出碼為 0。這份輸入完全是人造測試資料，不代表實測效能。重跑時請更換輸出檔名；工具不覆寫已有檔案或連結。

Windows EXE 已交叉編譯為 x86-64 PE32+ 並檢查匯入表，但本次沒有在 Windows 主機實際執行，亦未驗證 Windows 檔案 ACL。需要現代 Windows 及其 Universal C Runtime。EXE 未做 Authenticode 簽章；若公司政策不允許未簽章執行檔，請依政策處理或自行從原始碼建置，不要把雜湊驗證誤當成程式簽章。

## Linux x86-64

使用對應 Linux 套件中的 `./smlt`；是否為靜態 musl 版本及實際測試範圍以套件 `VALIDATION.md` 與 `BUILD_PROVENANCE.json` 為準。

```sh
chmod +x ./smlt
./smlt --help
./smlt iv-grid-audit examples/iv-grid-audit/synthetic_equal.csv --voltage-unit V --output grid-report.json
```

## 一次試跑 10 個核心工具

- Windows：閱讀後執行 `scripts/run-demo.ps1`。
- Linux：執行 `sh scripts/run-demo.sh`。
- 兩個腳本只呼叫本套件的原生執行檔，使用隨附人造資料，建立新的報告資料夾。沒有下載、Python 或外部服務。

## 工具選擇

| 指令 | 用途 |
|---|---|
| `iv-readpoint` | 指定非零讀取電壓的帶符號電流與靜態 I/V；不外插 |
| `rram-window` | 明確標示的 HRS/LRS 配對與電阻比；不推測狀態 |
| `tlm-extract` | 已接受線性 TLM 假設的片電阻／接觸電阻描述 |
| `vth-constant-current` | 使用者指定的帶符號定電流準則所對應 VGS |
| `hall-response` | 固定已宣告電流／溫度的 Hall 響應線性描述 |
| `sweep-segments` | 原始順序中的精確遞增、遞減及平坦座標邊段 |
| `transfer-log-slope` | 使用者指定電壓窗內 log10 電流的斜率；80 位有效數字對數 |
| `iv-loop-integral` | 精確電壓閉合路徑的帶符號 I dV 描述；不是能量 |
| `bosch-identity-audit` | BOSCH 身分欄與通道名稱稽核；不讀取目標／感測數值 |
| `iv-grid-audit` | 已宣告掃描之精確電壓集合及重複／缺漏比較 |

每個指令都有 `--help`。完整欄位、限制及科學解讀見 `docs/reference/`。單位須明確提供，大小寫有別，例如 `mV`、`uA`。

## 兩個完整原生特徵流程

RRAM 會把明確宣告的 HRS/LRS I–V 分支轉成每個實體元件一列的特徵；FET 保留每個分支的定電流準則與指定視窗 log 斜率。

```powershell
.\smlt.exe rram-features --input-dir .\examples\measurement-to-features\fixtures --output-dir .\NEW-rram-features --max-bracket-span-V 0.11 --accept-measurement-assumptions
.\smlt.exe fet-features .\examples\fet-measurement-to-features\fixtures\transfer.csv --voltage-unit V --current-unit nA --criterion-current 50 --criterion-current-unit nA --current-polarity positive --max-bracket-span 0.1 --window-min 0.1 --window-max 0.4 --accept-measurement-assumptions --output-dir .\NEW-fet-features
```

輸出目錄必須不存在。FET 任何分支被任一抽取器拒絕時，只保留稽核檔，不輸出部分特徵族群。兩個流程皆直接呼叫 Rust 模組，不執行 Python；來源紀錄採 schema 2，誠實記錄 Rust 原始碼雜湊，不能與舊版 Python 來源紀錄混為一談。見 `docs/NATIVE_WORKFLOWS.md`。

## 退出碼、隱私與科學限制

- `0`：完成該工具所定義的檢查，不代表科學驗證成功。
- `1`：依工具契約有排除或發現，仍可能產生完整診斷 JSON。RRAM 隨附資料刻意產生 1。
- `2`：致命輸入、命令列、資源界限或輸出錯誤；不能把新檔案當成完整報告。
- `--stdout` 是明確選擇將完整身分、量測及結果輸出到終端。平常建議 `--output NEW.json`。
- 不改動輸入，不覆寫既有輸出，不傳送資料。Unix 新報告要求模式 0600；Windows 使用資料夾繼承 ACL，不能視為自動僅擁有者可讀。
- 為避免管線或特殊檔案卡住，Rust 版只接受一般檔案；命令列參數須有效 UTF-8。錯誤訊息刻意不回顯原始資料及路徑。
- 不是訓練好的 ML 模型、儀器驅動或元件模擬器；沒有實測驗證。分支、循環、條件、物理身分與獨立性皆不能僅由標籤證明。
- exact decimal/rational 決策與原始字串會保留；JSON 浮點摘要仍是 binary64。不能把浮點顯示的相等誤認為輸入精確相等。

## 從原始碼建置

安裝官方 Rust 工具鏈後，在原始碼套件根目錄：

```sh
cargo build --release --locked
cargo test --locked
```

原生平台執行檔位於 `target/release/smlt`，Windows 為 `smlt.exe`。Windows 本機 Rust MSVC 建置需官方 C++ Build Tools；本次雲端交叉編譯的確切版本、連結調整及授權見建置來源紀錄。原始碼測試中的 Python 僅用來比對既有參考實作，成品執行不依賴 Python。
