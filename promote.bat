@echo off
echo ========================================
echo Promoting TEST to production (main)...
echo ========================================

git checkout main
if %errorlevel% neq g goto error

git merge TEST -m "Promote TEST changes to main"
if %errorlevel% neq g goto error

git push origin main
if %errorlevel% neq g goto error

git checkout TEST
echo ========================================
echo SUCCESS: Main updated, pushed, and back on TEST!
echo ========================================
pause
exit

:error
echo.
echo [ERROR] Something went wrong or there is a conflict. Check git status.
pausev