# usage: . .\bin\agent-env.ps1
#
# Substitute these placeholders during setup (RUNBOOK Step 5):
#   <APP_ID>        — numeric App ID (Step 1 output)
#   <INSTALL_ID>    — numeric Installation ID (Step 3 output)
#   <engagement>    — engagement slug
#   <dev>           — developer slug

$AppId       = "<APP_ID>"
$InstallId   = "<INSTALL_ID>"
$AppSlug     = "<engagement>-<dev>-bot"
$KeychainKey = "github-app-$AppSlug-pem"
$Store       = "$env:USERPROFILE\.secrets"

$Secure = Import-Clixml "$Store\$KeychainKey.xml"
$Pem    = ConvertFrom-SecureString $Secure -AsPlainText

$Token = $Pem | python "$PSScriptRoot\app-token.py" $AppId $InstallId
Remove-Variable Pem, Secure

$env:GH_TOKEN             = $Token
$env:GIT_AUTHOR_NAME      = "$AppSlug[bot]"
$env:GIT_AUTHOR_EMAIL     = "$AppId+$AppSlug[bot]@users.noreply.github.com"
$env:GIT_COMMITTER_NAME   = $env:GIT_AUTHOR_NAME
$env:GIT_COMMITTER_EMAIL  = $env:GIT_AUTHOR_EMAIL
