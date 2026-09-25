.PHONY: build up down stop restart ps logs health cells check

build:
	docker compose build

up:
	docker compose up -d --build

down:
	docker compose down

stop:
	docker compose stop

restart:
	docker compose restart

ps:
	docker compose ps

logs:
	docker compose logs -f

health:
	curl.exe -s http://localhost:8000/health

cells:
	curl.exe -s http://localhost:8000/api/cells

check:
	powershell -ExecutionPolicy Bypass -File requests/check-status.ps1