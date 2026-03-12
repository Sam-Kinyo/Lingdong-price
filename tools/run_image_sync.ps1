param(
    [string]$InputExcel = "c:\Users\郭庭豪\Desktop\暫存\LingDong商品總表.xlsx",
    [string]$FirebaseCred = "D:\keys\lingdong-price-admin.json",
    [int]$Workers = 3,
    [double]$MinHostInterval = 0.8,
    [switch]$OnlyNew
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$crawlerPath = Join-Path $projectRoot "tools\fetch_product_images.py"

if (!(Test-Path $crawlerPath)) {
    throw "找不到爬蟲腳本：$crawlerPath"
}
if (!(Test-Path $InputExcel)) {
    throw "找不到 Excel：$InputExcel"
}
if (!(Test-Path $FirebaseCred)) {
    throw "找不到 Firebase 憑證：$FirebaseCred"
}

$args = @(
    $crawlerPath,
    "--input", $InputExcel,
    "--workers", "$Workers",
    "--min-host-interval", "$MinHostInterval",
    "--no-save-local",
    "--upload-to-storage",
    "--update-firestore",
    "--firebase-cred", $FirebaseCred,
    "--firebase-bucket", "lingdong-price.firebasestorage.app",
    "--expected-project-id", "lingdong-price",
    "--progress"
)

if ($OnlyNew) {
    $args += "--only-new"
} else {
    $args += "--no-only-new"
}

Write-Host "[RUN] Start image sync..."
Write-Host "  Excel: $InputExcel"
Write-Host "  Cred : $FirebaseCred"
Write-Host "  Mode : " -NoNewline
if ($OnlyNew) { Write-Host "only-new" } else { Write-Host "full-refresh" }
Write-Host ""

python @args
if ($LASTEXITCODE -ne 0) {
    throw "爬蟲執行失敗，exit_code=$LASTEXITCODE"
}

Write-Host ""
Write-Host "[OK] 圖片已同步到 Firebase Storage + Firestore。"
