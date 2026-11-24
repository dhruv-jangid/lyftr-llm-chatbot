.PHONY: up down logs restart test ps

up:
	@test -f .env || (echo "No .env found. Run: cp .env.example .env  and add your keys." && exit 1)
	docker compose up -d --build
	@echo "Frontend: http://localhost:3000   Backend: http://localhost:8000/health"

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

restart: down up

# Runs the backend tests inside a throwaway Python container (no local Python needed).
test:
	docker run --rm -v "$(PWD)/backend:/app" -w /app python:3.12-slim sh -c "pip install -q -r requirements-dev.txt && python -m pytest"
