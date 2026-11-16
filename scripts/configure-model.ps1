$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = '证据助手 - 配置开发环境变量'
Write-Host '本窗口只配置一次当前Windows用户的DEEPSEEK_API_KEY，不运行模型。'
Write-Host 'Key不会显示、写入命令行参数或项目文件。以后开发会话直接读取环境变量。'
$secureKey = Read-Host '请输入DeepSeek API Key（隐藏输入）' -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer).Trim()
    if ($plainKey -notmatch '^[A-Za-z0-9_-]{8,200}$') {
        throw 'Key格式不符合条件；未保存，也没有发送请求。'
    }
    [Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', $plainKey, 'User')
    Write-Host '用户环境变量已配置。请返回Codex回复“已配置”，不要发送Key。'
} finally {
    $plainKey = $null
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    $secureKey.Dispose()
}
Read-Host '本窗口没有模型任务；按回车结束本配置窗口'
