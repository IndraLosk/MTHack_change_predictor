.PHONY: setup build up front down stop restart ps logs health cells db-psql db-show meta

# Один раз: загрузить образ эмулятора NDTP из локального tar + скачать Grafana из Docker Hub.
setup:
	docker image inspect ndtp-telemetry-emulator:1.0 >/dev/null 2>&1 || docker load -i dataset/ndtp-telemetry-emulator.tar
	docker image inspect grafana/grafana:11.2.0 >/dev/null 2>&1 || docker pull grafana/grafana:11.2.0 || echo "WARN: не удалось скачать grafana/grafana:11.2.0 (для \`make up\` с Grafana нужен доступ к Docker Hub)"

build:
	docker compose build

# Поднять ВСЁ: эмулятор, БД, backend, ML-воркер и BI-дашборд Grafana.
# Образ эмулятора локальный (из tar), Grafana подтягивается из Docker Hub (см. make setup).
up:
	docker compose up -d --build
	@echo ""
	@echo "API:        http://localhost:8000  (Swagger: /docs)"
	@echo "Grafana:    http://localhost:3000  (admin/admin) -> дашборд 'Диспетчерская'"

# Только Grafana (если ядро уже поднято через docker compose up отдельными сервисами).
front:
	docker compose up -d --build grafana
	@echo ""
	@echo "Grafana: http://localhost:3000  (admin/admin)"

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
	curl -s http://localhost:8000/api/cells

db-psql:
	docker compose exec db psql -U mthack -d mthack

db-show:
	docker compose exec db psql -U mthack -d mthack -c "\dt"

meta:
	@echo "API:        http://localhost:8000/docs"
	@echo "Grafana:    http://localhost:3000  (admin/admin)"
	@echo "PostgreSQL: localhost:5433 / mthack / mthack"