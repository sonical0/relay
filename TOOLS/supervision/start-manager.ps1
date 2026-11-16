# Starts the Claude Code session of a supervision manager (or worker) with the right environment.
#
#   powershell -NoExit -File TOOLS\supervision\start-manager.ps1 -Role A -Project example-app -Dir "C:\path\to\example-app" -PromptFile "C:\path\to\mgr-A.txt"
#
# By default Claude Code uses its own default configuration. -Config <dir> sets CLAUDE_CONFIG_DIR for this session
# only, e.g. to run the managers under another account or settings directory.
param(
  [Parameter(Mandatory = $true)][string]$Role,
  [Parameter(Mandatory = $true)][string]$Project,
  [Parameter(Mandatory = $true)][string]$Dir,
  [string]$PromptFile = '',
  [string]$Config = ''
)

Set-Location -LiteralPath $Dir
if ($Config) { $env:CLAUDE_CONFIG_DIR = $Config }
# No prompt suggestions: their grey text in the input box looks like a message typed but not sent, and the supervisor
# (read-screen) and the user can take them for real replies while the manager is waiting.
$env:CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION = 'false'
$env:SUP_AGENT = "$Project/$Role"
$env:SUP_PROJECT = $Project
# Launch token of the running supervision (written by server.py in run/<project>/run.id): the push guard and the board
# ignore a session from another launch. No file (older server): no token, previous behaviour.
$stateRoot = if ($env:SUP_STATE_ROOT) { $env:SUP_STATE_ROOT } else { Join-Path $PSScriptRoot 'run' }
$runFile = Join-Path (Join-Path $stateRoot $Project) 'run.id'
if (Test-Path -LiteralPath $runFile) { $env:SUP_RUN = (Get-Content -Raw -LiteralPath $runFile).Trim() }
else { Remove-Item Env:\SUP_RUN -ErrorAction SilentlyContinue }
Write-Host "Manager $Role ($Project) - configuration: $(if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { 'default' })"

# The state hooks (sup-hook.js) are loaded for this session only, never in the global settings (cost for every other session).
$supSettings = node (Join-Path $PSScriptRoot 'sup-hook.js') --emit-settings
$extra = if ($supSettings -and (Test-Path -LiteralPath $supSettings)) { @('--settings', $supSettings) } else { @() }
if ($PromptFile) { claude @extra (Get-Content -Raw -LiteralPath $PromptFile) } else { claude @extra }
