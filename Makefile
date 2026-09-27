.PHONY: setup build up down stop restart ps logs health cells db-psql db-show

setup:
	docker image inspect ndtp-telemetry-emulator:1.0 >/dev/null 2>&1 || docker load -i dataset/ndtp-telemetry-emulator.tar

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
	curl -s http://localhost:8000/health

cells:
	curl.exe -s http://localhost:8000/api/cells

db-psql:
	docker compose exec db psql -U mthack -d mthack

db-show:
	docker compose exec db psql -U mthack -d mthack -c "\dt"