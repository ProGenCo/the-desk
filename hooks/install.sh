#!/usr/bin/env bash
# Install hooks into .git/hooks/ as symlinks (so edits in repo propagate).
# Run once per clone: ./hooks/install.sh

set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

for hook in hooks/*; do
    name="$(basename "$hook")"
    [[ "$name" == "install.sh" || "$name" == "README.md" ]] && continue
    chmod +x "$hook"
    ln -sf "../../$hook" ".git/hooks/$name"
    echo "installed: $name → $hook"
done

echo "done."
