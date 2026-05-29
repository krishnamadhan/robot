#!/bin/bash
# sync_memory.sh — push session-state.md to krishnamadhan/claude-memory
# Run at end of every Claude session for cross-platform continuity.
# Usage: ~/robot/tools/sync_memory.sh

set -e

MEMORY_DIR="$HOME/.claude/projects/-home-pi/memory"
SESSION_FILE="$HOME/.claude/session-state.md"
ROBOT_DIR="$HOME/robot"

echo "→ Syncing session state to claude-memory git..."

# Pull latest first (in case another device made changes)
cd "$MEMORY_DIR"
git pull origin master --quiet 2>/dev/null || echo "  (pull failed — continuing with local)"

# Copy session-state.md into the memory repo
cp "$SESSION_FILE" "$MEMORY_DIR/session_state.md"

# Also write a project_robot_snapshot.md from current PROJECT_STATE.md header
if [ -f "$ROBOT_DIR/docs/PROJECT_STATE.md" ]; then
    echo "# Robot Project — Auto-snapshot" > "$MEMORY_DIR/project_robot_snapshot.md"
    echo "**Source:** robot/docs/PROJECT_STATE.md (live truth)" >> "$MEMORY_DIR/project_robot_snapshot.md"
    echo "**Synced:** $(date '+%Y-%m-%d %H:%M IST')" >> "$MEMORY_DIR/project_robot_snapshot.md"
    echo "" >> "$MEMORY_DIR/project_robot_snapshot.md"
    head -100 "$ROBOT_DIR/docs/PROJECT_STATE.md" >> "$MEMORY_DIR/project_robot_snapshot.md"
fi

# Update MEMORY.md to include new entries if not already there
if ! grep -q "session_state" "$MEMORY_DIR/MEMORY.md"; then
    echo "" >> "$MEMORY_DIR/MEMORY.md"
    echo "# Session" >> "$MEMORY_DIR/MEMORY.md"
    echo "- [Session State](session_state.md) — last session: what was done, next priority, system state, blockers" >> "$MEMORY_DIR/MEMORY.md"
    echo "- [Robot Snapshot](project_robot_snapshot.md) — auto-copied from robot/docs/PROJECT_STATE.md header" >> "$MEMORY_DIR/MEMORY.md"
fi

# Commit and push
cd "$MEMORY_DIR"
git add -A
TIMESTAMP=$(date '+%Y-%m-%dT%H:%M:%S')
git commit -m "session-sync $TIMESTAMP" --quiet 2>/dev/null || echo "  (nothing changed)"
git push origin master --quiet && echo "✅ Pushed to krishnamadhan/claude-memory" || echo "⚠️  Push failed (offline?)"

echo "→ Done. Session state is now in GitHub."
