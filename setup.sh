#!/bin/bash
set -e

echo "==> Starting PostgreSQL..."
docker compose up -d
sleep 3

echo "==> Installing Python dependencies..."
cd agents
python3 -m venv .venv
source .venv/bin/activate
pip install -r ../requirements.txt --quiet
cd ..

echo ""
echo "Setup complete. Next steps:"
echo "  1. Fill in .env.local with your ANTHROPIC_API_KEY and BROWSE_AI keys"
echo "  2. Run: npm run dev"
echo "  3. Visit: http://localhost:3000"
echo ""
echo "To run a refresh:"
echo "  Use the 'Refresh Now' button in the dashboard, or:"
echo "  cd agents && source .venv/bin/activate && python orchestrator.py"
