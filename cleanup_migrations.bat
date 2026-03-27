@echo off
echo Cleaning up broken migrations...

REM Delete any broken migration files (0008 and 0009 series)
if exist "apps\accounts\migrations\0008_*.py" (
    echo Deleting broken 0008 migrations...
    del /Q "apps\accounts\migrations\0008_*.py"
)

if exist "apps\accounts\migrations\0009_*.py" (
    echo Deleting broken 0009 migrations...
    del /Q "apps\accounts\migrations\0009_*.py"
)

echo.
echo Cleanup complete!
echo Now run: python manage.py migrate
echo Then run: python manage.py makemigrations
echo Then run: python manage.py migrate again
echo.
pause
