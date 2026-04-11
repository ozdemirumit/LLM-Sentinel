@echo off
set ENVIRONMENT=testing
python -m pytest tests/ -v --tb=short %*
