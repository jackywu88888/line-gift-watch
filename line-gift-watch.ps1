#Requires -Version 5.1
<#
.SYNOPSIS
  檢查 LINE 禮物公開活動頁是否已上線（不需登入）。

.DESCRIPTION
  只查 slugs.txt 的已知路徑，組合「這個月 + 下個月」。
  結果寫入 latest-live.txt；有新頁或每次檢查結束都會跳出通知。
  排程只在 09:00、18:00 執行，錯過不會開機補跑。

.EXAMPLE
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\line-gift-watch.ps1
.EXAMPLE
  .\line-gift-watch.ps1 -RegisterTask
.EXAMPLE
  .\line-gift-watch.ps1 -ShowResults
#>
param(
    [switch]$RegisterTask,
    [switch]$UnregisterTask,
    [switch]$ShowResults,
    [int]$DelaySeconds = 2
)

$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$TaskName = 'LINE Gift Watch'
$BaseUrl = 'https://gift-shop.landpress.line.me'
$Root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$SlugFile = Join-Path $Root 'slugs.txt'
$StateFile = Join-Path $Root 'state.json'
$LogFile = Join-Path $Root 'watch.log'
$LiveFile = Join-Path $Root 'latest-live.txt'
$NewFile = Join-Path $Root 'new-hits.txt'
$CatalogFile = Join-Path $Root 'catalog.json'
$CatalogTextFile = Join-Path $Root 'catalog.txt'
$KeywordPattern = '新朋友|1元|1點|心意禮'

function Write-Utf8File {
    param([string]$Path, [string]$Content, [switch]$Append)
    $utf8 = New-Object System.Text.UTF8Encoding $false
    if ($Append) {
        [System.IO.File]::AppendAllText($Path, $Content, $utf8)
    } else {
        [System.IO.File]::WriteAllText($Path, $Content, $utf8)
    }
}

function Write-Log {
    param([string]$Message)
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Output $line
    Write-Utf8File -Path $LogFile -Content ($line + [Environment]::NewLine) -Append
}

function Show-Notify {
    param([string]$Title, [string]$Message)
    if ($Title.Length -gt 60) { $Title = $Title.Substring(0, 60) + '...' }
    if ($Message.Length -gt 200) { $Message = $Message.Substring(0, 200) + '...' }

    $toastOk = $false
    try {
        $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
        $escapedTitle = [System.Security.SecurityElement]::Escape($Title)
        $escapedBody = [System.Security.SecurityElement]::Escape($Message)
        $xmlText = @"
<toast><visual><binding template="ToastGeneric"><text>$escapedTitle</text><text>$escapedBody</text></binding></visual></toast>
"@
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($xmlText)
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('LINE Gift Watch')
        $notifier.Show($toast)
        $toastOk = $true
    } catch {
        $toastOk = $false
    }

    if (-not $toastOk) {
        try {
            Add-Type -AssemblyName System.Windows.Forms | Out-Null
            Add-Type -AssemblyName System.Drawing | Out-Null
            $notify = New-Object System.Windows.Forms.NotifyIcon
            $notify.Icon = [System.Drawing.SystemIcons]::Information
            $notify.Visible = $true
            $notify.ShowBalloonTip(10000, $Title, $Message, [System.Windows.Forms.ToolTipIcon]::Info)
            Start-Sleep -Seconds 2
            $notify.Dispose()
        } catch {
            Write-Log ("通知失敗: {0}" -f $_.Exception.Message)
        }
    }
}

function Get-WatchMonths {
    $now = Get-Date
    @(
        $now.ToString('yyyyMM'),
        $now.AddMonths(1).ToString('yyyyMM')
    )
}

function Get-SlugList {
    $slugs = @()
    if (Test-Path $SlugFile) {
        Get-Content -Path $SlugFile -Encoding UTF8 |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and ($_ -notmatch '^\s*#') } |
            ForEach-Object { $slugs += $_ }
    }
    if ($slugs.Count -eq 0) {
        $slugs = @(
            'family_icecream',
            '7-11_coffee',
            '7-11_breakfast',
            '7-11_1dollarcafe',
            'wootea_drinks',
            'KFC_Eggtart',
            '1point',
            '1dollar'
        )
    }
    $slugs | Select-Object -Unique
}

function Get-Utf8Page {
    param([string]$Url)
    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.Method = 'GET'
    $request.Timeout = 20000
    $request.ReadWriteTimeout = 20000
    $request.AllowAutoRedirect = $false
    $request.UserAgent = 'Mozilla/5.0 LineGiftWatch/1.0'
    $request.Accept = 'text/html'
    try {
        $response = $request.GetResponse()
        try {
            $stream = $response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            $html = $reader.ReadToEnd()
            $reader.Close()
            return [pscustomobject]@{
                Status = [int]$response.StatusCode
                Html   = $html
            }
        } finally {
            $response.Close()
        }
    } catch [System.Net.WebException] {
        $webResp = $_.Exception.Response
        if ($webResp) {
            $code = [int]$webResp.StatusCode
            $webResp.Close()
            return [pscustomobject]@{ Status = $code; Html = '' }
        }
        throw
    }
}

function Get-ProductIdsFromHtml {
    param([string]$Html)
    $ids = New-Object System.Collections.Generic.List[string]
    $idMatches = [regex]::Matches($Html, '(?:giftshop-tw\.line\.me|liff\.line\.me/[^"\s]+)/products/(\d+)')
    foreach ($m in $idMatches) {
        $id = $m.Groups[1].Value
        if ($id -and -not $ids.Contains($id)) { $ids.Add($id) }
    }
    @($ids)
}

function Get-SlugFromCampaignUrl {
    param([string]$Url)
    if ($Url -match '/(\d{6})_(.+?)/?$') {
        return [pscustomobject]@{ Month = $Matches[1]; Slug = $Matches[2]; Campaign = ($Matches[1] + '_' + $Matches[2]) }
    }
    return $null
}

function Read-Catalog {
    $list = New-Object System.Collections.Generic.List[object]
    if (Test-Path $CatalogFile) {
        try {
            $raw = Get-Content -Path $CatalogFile -Raw -Encoding UTF8
            if (-not [string]::IsNullOrWhiteSpace($raw)) {
                $parsed = $raw | ConvertFrom-Json
                if ($parsed) {
                    foreach ($row in @($parsed)) { $list.Add($row) }
                }
            }
        } catch {
            Write-Log ("讀取 catalog.json 失敗，改用空白目錄: {0}" -f $_.Exception.Message)
        }
    }
    $seeds = @(
        [pscustomobject]@{ slug = '7-11_coffee'; productId = '322419346'; title = '[新客限定1元體驗品] 【7-ELEVEN】CITY CAFE熱拿鐵(中)好禮即享券'; campaign = ''; productUrl = 'https://giftshop-tw.line.me/products/322419346'; firstSeen = ''; lastSeen = '' },
        [pscustomobject]@{ slug = 'family_icecream'; productId = '322498511'; title = '[新客限定1元體驗品] 【全家】 Fami霜淇淋 (口味不限)'; campaign = ''; productUrl = 'https://giftshop-tw.line.me/products/322498511'; firstSeen = ''; lastSeen = '' }
    )
    foreach ($seed in $seeds) {
        $exists = $false
        foreach ($row in $list) {
            if ([string]$row.productId -eq $seed.productId) { $exists = $true; break }
        }
        if (-not $exists) { $list.Add($seed) }
    }
    return ,$list
}

function Write-Catalog {
    param($List)
    $normalized = New-Object System.Collections.Generic.List[object]
    foreach ($row in $List) {
        $normalized.Add([pscustomobject]@{
            slug       = [string]$row.slug
            productId  = [string]$row.productId
            title      = [string]$row.title
            campaign   = [string]$row.campaign
            productUrl = [string]$row.productUrl
            firstSeen  = [string]$row.firstSeen
            lastSeen   = [string]$row.lastSeen
        })
    }
    $array = $normalized.ToArray()
    $json = ConvertTo-Json -InputObject $array -Depth 6
    Write-Utf8File -Path $CatalogFile -Content $json

    $bySlug = @{}
    foreach ($row in $array) {
        $key = $row.slug
        if (-not $bySlug.ContainsKey($key)) { $bySlug[$key] = New-Object System.Collections.Generic.List[object] }
        $bySlug[$key].Add($row)
    }
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('1元商品目錄（同一款會重複上架，productId 每次不同）')
    $lines.Add(('更新時間: {0}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')))
    $lines.Add('')
    foreach ($key in ($bySlug.Keys | Sort-Object)) {
        $lines.Add(('[{0}]' -f $key))
        foreach ($row in ($bySlug[$key] | Sort-Object productId)) {
            $lines.Add(('  {0}  {1}  {2}' -f $row.productId, $row.campaign, $row.title))
        }
        $lines.Add('')
    }
    Write-Utf8File -Path $CatalogTextFile -Content (($lines -join [Environment]::NewLine) + [Environment]::NewLine)
}

function Upsert-Catalog {
    param(
        [System.Collections.IList]$List,
        [string]$Slug,
        [string]$ProductId,
        [string]$Title,
        [string]$Campaign,
        [string]$Now
    )
    if (-not $ProductId) {
        return [pscustomobject]@{ NewType = $false; NewId = $false }
    }
    $knownSlug = $false
    $knownId = $false
    $hit = $null
    foreach ($row in $List) {
        if ([string]$row.slug -eq $Slug) { $knownSlug = $true }
        if ([string]$row.productId -eq $ProductId) { $knownId = $true; $hit = $row }
    }
    if ($hit) {
        $hit.lastSeen = $Now
        if ($Title) { $hit.title = $Title }
        if ($Campaign) { $hit.campaign = $Campaign }
        if (-not $hit.productUrl) { $hit.productUrl = "https://giftshop-tw.line.me/products/$ProductId" }
    } else {
        $List.Add([pscustomobject]@{
            slug       = $Slug
            productId  = $ProductId
            title      = $Title
            campaign   = $Campaign
            productUrl = "https://giftshop-tw.line.me/products/$ProductId"
            firstSeen  = $Now
            lastSeen   = $Now
        })
    }
    [pscustomobject]@{
        NewType = -not $knownSlug
        NewId   = -not $knownId
    }
}

function Get-PageMeta {
    param([string]$Html)
    $text = [regex]::Replace($Html, '<script[\s\S]*?</script>', ' ', 'IgnoreCase')
    $text = [regex]::Replace($text, '<style[\s\S]*?</style>', ' ', 'IgnoreCase')
    $text = [regex]::Replace($text, '<[^>]+>', ' ')
    $text = [System.Net.WebUtility]::HtmlDecode($text)
    $text = [regex]::Replace($text, '\s+', ' ').Trim()

    $title = ''
    if ($Html -match 'property="og:title"\s+content="([^"]+)"') {
        $title = [System.Net.WebUtility]::HtmlDecode($Matches[1])
    } elseif ($Html -match '<title>([^<]+)</title>') {
        $title = [System.Net.WebUtility]::HtmlDecode($Matches[1])
    }

    $desc = ''
    if ($Html -match 'property="og:description"\s+content="([^"]+)"') {
        $desc = [System.Net.WebUtility]::HtmlDecode($Matches[1])
    }

    $period = ''
    if ($text -match '(\d{4}年\d{1,2}月\d{1,2}日.{0,12}00:00\s*[-~～]\s*\d{4}年\d{1,2}月\d{1,2}日.{0,12}23:59)') {
        $period = $Matches[1].Trim()
    }

    [pscustomobject]@{
        Title  = $title.Trim()
        Desc   = $desc.Trim()
        Period = $period
        Text   = $text
    }
}

function Read-State {
    if (-not (Test-Path $StateFile)) {
        return @{}
    }
    try {
        $raw = Get-Content -Path $StateFile -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($raw)) { return @{} }
        $obj = $raw | ConvertFrom-Json
        $map = @{}
        foreach ($prop in $obj.PSObject.Properties) {
            $map[$prop.Name] = $prop.Value
        }
        return $map
    } catch {
        Write-Log ("讀取 state.json 失敗，改用空白狀態: {0}" -f $_.Exception.Message)
        return @{}
    }
}

function Write-State {
    param($Map)
    $ordered = [ordered]@{}
    foreach ($key in ($Map.Keys | Sort-Object)) {
        $ordered[$key] = $Map[$key]
    }
    $json = $ordered | ConvertTo-Json -Depth 6
    Write-Utf8File -Path $StateFile -Content $json
}

function Register-WatchTask {
    $scriptPath = Join-Path $Root 'line-gift-watch.ps1'
    $arg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`""
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
    $triggers = @(
        (New-ScheduledTaskTrigger -Daily -At 09:00),
        (New-ScheduledTaskTrigger -Daily -At 18:00)
    )
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew
    $settings.StartWhenAvailable = $false
    $settings.Hidden = $true
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description '檢查 LINE 禮物公開活動頁是否已上線（僅 09:00 / 18:00，不補跑）' -Force | Out-Null
    Write-Output "已安裝工作排程: $TaskName"
    Write-Output '時間: 每天 09:00、18:00。電腦當時沒開就跳過，不會開機補跑。'
    Write-Output '視窗: 隱藏。結果看 Windows 通知，或雙擊 看結果.cmd'
}

function Unregister-WatchTask {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "已移除工作排程: $TaskName"
}

if ($UnregisterTask) {
    Unregister-WatchTask
    exit 0
}

if ($RegisterTask) {
    Register-WatchTask
    exit 0
}

if ($ShowResults) {
    if (Test-Path $LiveFile) {
        notepad.exe $LiveFile
    } else {
        Write-Output '還沒有 latest-live.txt，請先執行一次檢查。'
    }
    exit 0
}

$months = Get-WatchMonths
$slugs = @(Get-SlugList)
$urls = foreach ($ym in $months) {
    foreach ($slug in $slugs) {
        '{0}/{1}_{2}/' -f $BaseUrl, $ym, $slug
    }
}

Write-Log ("開始檢查 {0} 個網址（月份 {1}）" -f $urls.Count, ($months -join ', '))

$state = Read-State
$catalog = Read-Catalog
$liveRows = New-Object System.Collections.Generic.List[string]
$liveTitles = New-Object System.Collections.Generic.List[string]
$newRows = New-Object System.Collections.Generic.List[string]
$newHits = New-Object System.Collections.Generic.List[object]
$nowIso = (Get-Date).ToString('o')

foreach ($url in $urls) {
    $status = $null
    $meta = $null
    $isCampaign = $false
    $productIds = @()
    $parsed = Get-SlugFromCampaignUrl -Url $url
    $slug = if ($parsed) { $parsed.Slug } else { '' }
    $campaign = if ($parsed) { $parsed.Campaign } else { '' }

    try {
        $page = Get-Utf8Page -Url $url
        $status = [int]$page.Status
        if ($status -eq 200 -and $page.Html) {
            $meta = Get-PageMeta -Html $page.Html
            $productIds = @(Get-ProductIdsFromHtml -Html $page.Html)
            $blob = '{0} {1} {2}' -f $meta.Title, $meta.Desc, $meta.Text
            $isCampaign = [bool]($blob -match $KeywordPattern)
        }
    } catch {
        $status = 0
        Write-Log ("失敗 {0}  {1}" -f $url, $_.Exception.Message)
    }

    $prev = $null
    if ($state.ContainsKey($url)) { $prev = $state[$url] }

    $productId = if ($productIds.Count -gt 0) { [string]$productIds[0] } else { '' }
    $productUrl = if ($productId) { "https://giftshop-tw.line.me/products/$productId" } else { '' }

    $catalogFlag = [pscustomobject]@{ NewType = $false; NewId = $false }
    if ($isCampaign -and $slug -and $productId) {
        $catalogFlag = Upsert-Catalog -List $catalog -Slug $slug -ProductId $productId -Title $(if ($meta) { $meta.Title } else { '' }) -Campaign $campaign -Now $nowIso
    }

    $entry = [ordered]@{
        status     = $status
        title      = if ($meta) { $meta.Title } else { '' }
        period     = if ($meta) { $meta.Period } else { '' }
        productId  = $productId
        isCampaign = $isCampaign
        firstSeen  = if ($prev -and $prev.firstSeen) { [string]$prev.firstSeen } else { $nowIso }
        lastSeen   = $nowIso
    }
    $state[$url] = [pscustomobject]$entry

    if ($status -eq 200 -and $isCampaign) {
        $blockLines = New-Object System.Collections.Generic.List[string]
        $blockLines.Add($url)
        $blockLines.Add($entry.title)
        if ($entry.period) { $blockLines.Add($entry.period) }
        if ($productUrl) { $blockLines.Add($productUrl) }
        if ($catalogFlag.NewType) {
            $blockLines.Add('比對: 新品項（目錄沒看過這個 slug）')
        } elseif ($catalogFlag.NewId) {
            $blockLines.Add(('比對: 已知商品重新上架，新ID {0}' -f $productId))
        } elseif ($productId) {
            $blockLines.Add(('比對: 已知商品、已知ID {0}' -f $productId))
        }
        $block = $blockLines -join [Environment]::NewLine
        $liveRows.Add($block)
        $liveRows.Add('')
        $liveTitles.Add($entry.title)

        $wasLive = $prev -and ([int]$prev.status -eq 200) -and (
            ($prev.isCampaign -eq $true) -or ([string]$prev.isCampaign -eq 'True')
        )
        $isNew = (-not $wasLive) -or $catalogFlag.NewId
        if ($isNew) {
            $newHits.Add([pscustomobject]@{ Url = $url; Title = $entry.title; Period = $entry.period; ProductId = $productId })
            $newRows.Add($block)
            $newRows.Add('')
            Write-Log ("新活動  {0}  id={1}  {2}" -f $url, $productId, $entry.title)
        } else {
            Write-Log ("仍在線  {0}  id={1}  {2}" -f $url, $productId, $entry.title)
        }
    } elseif ($status -eq 200) {
        Write-Log ("200 但非活動關鍵字  {0}  {1}" -f $url, $entry.title)
    } else {
        Write-Log ("{0}  {1}" -f $status, $url)
    }

    Start-Sleep -Seconds $DelaySeconds
}

Write-State -Map $state
Write-Catalog -List $catalog

$liveHeader = "更新時間: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`n`r`n"
if ($liveRows.Count -gt 0) {
    Write-Utf8File -Path $LiveFile -Content ($liveHeader + ($liveRows -join [Environment]::NewLine).Trim() + [Environment]::NewLine)
} else {
    Write-Utf8File -Path $LiveFile -Content ($liveHeader + '目前沒有已公開的已知活動頁。' + [Environment]::NewLine)
}

$summaryBody = if ($liveTitles.Count -gt 0) {
    ($liveTitles | Select-Object -Unique) -join [Environment]::NewLine
} else {
    '已知路徑目前沒有已公開活動頁。'
}

if ($newHits.Count -gt 0) {
    Write-Utf8File -Path $NewFile -Content ($liveHeader + ($newRows -join [Environment]::NewLine).Trim() + [Environment]::NewLine)
    $titles = ($newHits | ForEach-Object { $_.Title } | Where-Object { $_ }) -join ' / '
    if (-not $titles) { $titles = '{0} 場新活動頁' -f $newHits.Count }
    Show-Notify -Title $titles -Message $summaryBody
    Write-Log ("本輪新發現 {0} 場" -f $newHits.Count)
} else {
    Write-Utf8File -Path $NewFile -Content ($liveHeader + '本輪沒有新活動頁。' + [Environment]::NewLine)
    Show-Notify -Title ("檢查完成，目前 {0} 場已公開" -f $liveTitles.Count) -Message $summaryBody
    Write-Log '本輪沒有新活動頁'
}

Write-Log '檢查結束'
Write-Output ''
Write-Output ("結果已寫入: {0}" -f $LiveFile)
Write-Output ("1元目錄: {0}" -f $CatalogTextFile)
Write-Output '可雙擊 看結果.cmd 打開。'
