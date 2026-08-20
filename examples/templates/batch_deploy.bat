@echo off
REM Junos Push Configuration - Windows Batch Automation Script
REM This script demonstrates automated deployment across multiple groups

echo 🚀 Starting Junos Configuration Batch Deployment
echo ==================================================

set "CONFIGS=examples/interface_config.set examples/ospf_config.set examples/security_config.set"
set "GROUPS=core edge campus"

if "%1"=="dry-run" goto DRY_RUN
if "%1"=="deploy-confirmed" goto DEPLOY_CONFIRMED
if "%1"=="commit-all" goto COMMIT_ALL
if "%1"=="deploy-direct" goto DEPLOY_DIRECT
goto USAGE

:DRY_RUN
echo 🧪 Performing dry run on all groups...
for %%g in (%GROUPS%) do (
    for %%c in (%CONFIGS%) do (
        echo.
        echo Testing %%c on %%g...
        junos-push -g %%g -c %%c --dry-run
    )
)
goto END

:DEPLOY_CONFIRMED
echo 🛡️ Deploying with commit-confirmed (5 min auto-rollback)...
for %%g in (%GROUPS%) do (
    for %%c in (%CONFIGS%) do (
        echo.
        echo 📋 Deploying %%c to %%g group...
        junos-push -g %%g -c %%c -o commit-confirmed --backup -v
        if errorlevel 1 (
            echo 💥 Deployment failed, stopping batch operation
            exit /b 1
        )
        echo ⏳ Waiting 30 seconds before next deployment...
        timeout /t 30 /nobreak >nul
    )
)
echo.
echo ⚠️  Remember to confirm commits within 5 minutes or they will auto-rollback!
goto END

:COMMIT_ALL
echo ✅ Committing all confirmed configurations...
for %%g in (%GROUPS%) do (
    echo Committing configurations on %%g...
    junos-push -g %%g -c examples/interface_config.set -o commit
)
goto END

:DEPLOY_DIRECT
echo ⚠️  Direct deployment without confirmation - USE WITH CAUTION!
set /p confirm="Are you sure? Type 'YES' to continue: "
if not "%confirm%"=="YES" (
    echo Deployment cancelled
    goto END
)
for %%g in (%GROUPS%) do (
    for %%c in (%CONFIGS%) do (
        junos-push -g %%g -c %%c -o commit --backup -v
    )
)
goto END

:USAGE
echo Usage: %0 {dry-run^|deploy-confirmed^|commit-all^|deploy-direct}
echo.
echo Commands:
echo   dry-run         - Test all configurations without changes
echo   deploy-confirmed - Deploy with 5-minute auto-rollback
echo   commit-all      - Commit all pending confirmed configurations
echo   deploy-direct   - Deploy directly (dangerous, requires confirmation)
echo.
echo Examples:
echo   %0 dry-run
echo   %0 deploy-confirmed
exit /b 1

:END
echo.
echo 🎉 Batch operation completed!
