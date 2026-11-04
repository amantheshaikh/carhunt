#!/bin/bash
set -e

echo "=== Setting up CarHunt ==="

# Check prerequisites
command -v docker >/dev/null 2>&1 || { echo >&2 "Error: docker is required but not installed. Please install Docker Desktop."; exit 1; }
command -v node >/dev/null 2>&1 || { echo >&2 "Error: node is required but not installed. Please install Node.js (v18+)."; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo >&2 "Error: python3 is required but not installed. Please install Python 3.11+."; exit 1; }

# Initialize .env.local if not present
if [ ! -f .env.local ]; then
  echo "==> Creating .env.local from .env.example..."
  cp .env.example .env.local
else
  echo "==> Existing .env.local detected, preserving."
fi

# Start PostgreSQL
echo "==> Starting PostgreSQL..."
docker compose up -d

echo "==> Waiting for database to be ready..."
for i in {1..30}; do
  if docker compose exec -T db pg_isready -U carhunt -d carhunt >/dev/null 2>&1; then
    echo "    PostgreSQL is ready."
    break
  fi
  sleep 1
done

# Install Node dependencies
echo "==> Installing Node dependencies..."
npm install

# Set up Python virtual environment
echo "==> Setting up Python virtual environment..."
cd agents
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r ../requirements.txt --quiet
echo "==> Installing Playwright Chromium browser..."
playwright install chromium
cd ..

echo ""
echo "=========================================================="
echo "  CarHunt setup complete!"
echo "=========================================================="
echo ""
echo "Next steps:"
echo "  1. Add your OpenRouter API key to .env.local:"
echo "     OPENROUTER_API_KEY=your_key_here"
echo ""
echo "  2. Start the web dashboard:"
echo "     npm run dev"
echo ""
echo "  3. Open http://localhost:3000 in your browser"
echo "     Login credentials (configured in .env.local):"
echo "       Username: admin"
echo "       Password: carhunt"
echo ""
echo "  4. To run an initial scrape & model score:"
echo "     Click 'Refresh Now' in the dashboard, or run:"
echo "     cd agents && source .venv/bin/activate && python orchestrator.py"
echo "=========================================================="
