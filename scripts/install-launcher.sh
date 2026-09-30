#!/bin/zsh
# Builds ~/Applications/Job Search.app (openable from Spotlight / the Dock).
# Re-run if the project folder moves.
set -e
SCRIPT="$(cd "$(dirname "$0")" && pwd)/open-job-search.sh"
mkdir -p "$HOME/Applications"
osacompile -o "$HOME/Applications/Job Search.app" -e "do shell script quoted form of \"$SCRIPT\""
echo "Installed $HOME/Applications/Job Search.app"
