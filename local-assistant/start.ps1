param(
    [Parameter(Mandatory=$true)][string]$StrataCommand,
    [Parameter(Mandatory=$true)][string]$DataDirectory,
    [string]$ModelPath,
    [switch]$Plan,
    [switch]$Tune
)
$ErrorActionPreference = 'Stop'
$profilePath = Join-Path $PSScriptRoot 'rtx4060-64gb.json'
$profile = Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $StrataCommand -PathType Leaf)) { throw 'StrataCommand does not exist' }
$arguments = @('launch', '--family', $profile.family, '--model', $profile.model,
    '--context', $profile.context, '--vision', $profile.vision,
    '--parallel', [string]$profile.parallel, '--port', [string]$profile.port,
    '--access', 'local', '--data-dir', $DataDirectory,
    '--save-preset', $profile.profile_name, '--yes', '--no-tui')
foreach ($option in $profile.engine_options.PSObject.Properties) {
    $arguments += @('--set', ($option.Name + '=' + $option.Value))
}
if ($ModelPath) { $arguments += @('--model-path', $ModelPath) }
if ($Plan) { $arguments += @('--plan', '--output', 'json') }
if ($Tune) { $arguments += @('--tune', 'quick') }
& $StrataCommand @arguments
if ($LASTEXITCODE -ne 0) { throw "Strata exited with code $LASTEXITCODE" }
