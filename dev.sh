#!/bin/bash
# Start backend and frontend dev servers in separate tmux sessions.
# Usage: ./dev.sh

DIR="$(cd "$(dirname "$0")" && pwd)"

# Kill existing sessions if any
tmux kill-session -t medsecure-backend 2>/dev/null
tmux kill-session -t medsecure-frontend 2>/dev/null

# Start backend
tmux new-session -d -s medsecure-backend -c "$DIR/backend" \
  "uv run fastapi dev app/main.py"

# Start frontend
tmux new-session -d -s medsecure-frontend -c "$DIR/frontend" \
  "npm run dev"

echo "Dev servers started:"
echo "  Backend:  tmux attach -t medsecure-backend"
echo "  Frontend: tmux attach -t medsecure-frontend"
echo ""
echo "To stop both: ./dev.sh stop"

if [ "$1" = "stop" ]; then
  tmux kill-session -t medsecure-backend 2>/dev/null
  tmux kill-session -t medsecure-frontend 2>/dev/null
  echo "Stopped."
fi
